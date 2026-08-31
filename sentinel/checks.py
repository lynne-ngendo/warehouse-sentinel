"""The deterministic check layer.

Each check turns one contract from `contracts.md` into SQL. A check decides
pass or fail on its own. When it fails it runs a second query to collect
evidence, so the expensive detail work only happens on the rows that matter.

Nothing here calls a model. That separation is deliberate: a model that
misbehaves cannot invent an incident, and a model that is unavailable does not
stop detection.
"""

import concurrent.futures
import time
import traceback

from . import config
from .findings import CheckResult


def _pct(part, whole):
    return round(100.0 * part / whole, 2) if whole else 0.0


# --- fct_visit -------------------------------------------------------------

VISIT_GRAIN_SQL = """
SELECT COUNT(*) AS row_count, COUNT(DISTINCT visit_id) AS distinct_key
FROM {dataset}.fct_visit
"""

VISIT_GRAIN_EVIDENCE_SQL = """
SELECT
  CAST(visit_date AS STRING) AS visit_date,
  COUNT(*) - COUNT(DISTINCT visit_id) AS duplicate_rows,
  STRING_AGG(DISTINCT source_batch ORDER BY source_batch LIMIT 5) AS batches
FROM {dataset}.fct_visit
GROUP BY visit_date
HAVING duplicate_rows > 0
ORDER BY duplicate_rows DESC
LIMIT 10
"""

FRESHNESS_SQL = """
SELECT
  CAST(MAX(visit_date) AS STRING) AS max_visit_date,
  DATE_DIFF(CURRENT_DATE(), MAX(visit_date), DAY) AS lag_days
FROM {dataset}.fct_visit
"""

LIVENESS_SQL = """
SELECT
  source_system,
  CAST(MAX(visit_date) AS STRING) AS max_visit_date,
  DATE_DIFF(CURRENT_DATE(), MAX(visit_date), DAY) AS lag_days,
  COUNT(*) AS rows_in_window
FROM {dataset}.fct_visit
WHERE visit_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
GROUP BY source_system
ORDER BY lag_days DESC
"""

COVERAGE_SQL = """
WITH baseline AS (
  SELECT DISTINCT unit_id
  FROM {dataset}.fct_visit
  WHERE visit_date BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL {baseline} DAY)
                       AND DATE_SUB(CURRENT_DATE(), INTERVAL {trailing} DAY)
),
recent AS (
  SELECT DISTINCT unit_id
  FROM {dataset}.fct_visit
  WHERE visit_date > DATE_SUB(CURRENT_DATE(), INTERVAL {trailing} DAY)
)
SELECT
  (SELECT COUNT(*) FROM baseline) AS baseline_units,
  (SELECT COUNT(*) FROM recent) AS recent_units,
  ARRAY(
    SELECT unit_id FROM baseline
    WHERE unit_id NOT IN (SELECT unit_id FROM recent)
    ORDER BY unit_id
  ) AS missing_units
"""

COVERAGE_EVIDENCE_SQL = """
SELECT
  v.unit_id,
  ANY_VALUE(u.unit_name) AS unit_name,
  ANY_VALUE(u.subcounty) AS subcounty,
  CAST(MAX(v.visit_date) AS STRING) AS last_visit_date,
  DATE_DIFF(CURRENT_DATE(), MAX(v.visit_date), DAY) AS days_silent,
  COUNT(*) AS visits_before_going_silent
FROM {dataset}.fct_visit v
LEFT JOIN {dataset}.dim_unit u ON v.unit_id = u.unit_id
WHERE v.visit_date > DATE_SUB(CURRENT_DATE(), INTERVAL {baseline} DAY)
GROUP BY v.unit_id
HAVING days_silent > {trailing}
ORDER BY days_silent DESC, v.unit_id
LIMIT 25
"""

REFERENTIAL_SQL = """
SELECT
  COUNT(*) AS total_visits,
  COUNTIF(u.unit_id IS NULL) AS orphan_unit,
  COUNTIF(w.worker_id IS NULL) AS orphan_worker,
  COUNTIF(h.household_id IS NULL) AS orphan_household
FROM {dataset}.fct_visit v
LEFT JOIN {dataset}.dim_unit u ON v.unit_id = u.unit_id
LEFT JOIN {dataset}.dim_worker w ON v.worker_id = w.worker_id
LEFT JOIN {dataset}.fct_household h ON v.household_id = h.household_id
"""

# --- fct_household ---------------------------------------------------------

TOMBSTONE_SQL = """
SELECT
  COUNT(*) AS total_households,
  COUNTIF(deleted_at IS NOT NULL) AS tombstoned,
  COUNTIF(deleted_at IS NOT NULL AND is_active) AS leaked
FROM {dataset}.fct_household
"""

TOMBSTONE_EVIDENCE_SQL = """
SELECT
  household_id,
  unit_id,
  CAST(deleted_at AS STRING) AS deleted_at,
  is_active
FROM {dataset}.fct_household
WHERE deleted_at IS NOT NULL AND is_active
ORDER BY deleted_at DESC
LIMIT 10
"""

HOUSEHOLD_GRAIN_SQL = """
SELECT COUNT(*) AS row_count, COUNT(DISTINCT household_id) AS distinct_key
FROM {dataset}.fct_household
"""


def check_visit_grain(wh):
    row = wh.scalar(VISIT_GRAIN_SQL)
    dupes = row["row_count"] - row["distinct_key"]
    passed = dupes == 0
    evidence = dict(row)
    if not passed:
        evidence["duplicate_rows"] = dupes
        evidence["inflation_pct"] = _pct(dupes, row["distinct_key"])
        evidence["worst_dates"] = wh.query(VISIT_GRAIN_EVIDENCE_SQL)
    summary = (
        "visit_id is unique"
        if passed
        else f"{dupes:,} duplicate rows inflate counts by "
        f"{evidence['inflation_pct']}%"
    )
    return passed, evidence, summary


def check_visit_freshness(wh):
    row = wh.scalar(FRESHNESS_SQL)
    passed = row["lag_days"] <= config.FRESHNESS_MAX_LAG_DAYS
    summary = (
        f"latest visit is {row['max_visit_date']}, {row['lag_days']} day(s) old"
    )
    return passed, dict(row), summary


def check_source_liveness(wh):
    rows = wh.query(LIVENESS_SQL)
    stale = [r for r in rows if r["lag_days"] > config.LIVENESS_MAX_LAG_DAYS]
    passed = not stale
    evidence = {"sources": rows, "stale_sources": stale}
    if passed:
        summary = f"all {len(rows)} sources reported within " \
                  f"{config.LIVENESS_MAX_LAG_DAYS} day(s)"
    else:
        names = ", ".join(
            f"{r['source_system']} (silent {r['lag_days']}d)" for r in stale
        )
        summary = f"{len(stale)} of {len(rows)} sources stopped: {names}"
    return passed, evidence, summary


def check_unit_coverage(wh):
    row = wh.scalar(
        COVERAGE_SQL,
        baseline=int(config.BASELINE_DAYS),
        trailing=int(config.TRAILING_DAYS),
    )
    missing = list(row.get("missing_units") or [])
    passed = not missing
    evidence = {
        "baseline_units": row["baseline_units"],
        "recent_units": row["recent_units"],
        "missing_units": missing,
        "missing_pct": _pct(len(missing), row["baseline_units"]),
    }
    if not passed:
        evidence["detail"] = wh.query(
            COVERAGE_EVIDENCE_SQL,
            baseline=int(config.BASELINE_DAYS),
            trailing=int(config.TRAILING_DAYS),
        )
    summary = (
        f"all {row['baseline_units']} units still reporting"
        if passed
        else f"{len(missing)} of {row['baseline_units']} units stopped "
        f"reporting ({evidence['missing_pct']}%)"
    )
    return passed, evidence, summary


def check_referential_integrity(wh):
    row = wh.scalar(REFERENTIAL_SQL)
    orphans = row["orphan_unit"] + row["orphan_worker"] + row["orphan_household"]
    passed = orphans == 0
    summary = (
        "every key resolves"
        if passed
        else f"{orphans:,} visits reference a missing dimension row"
    )
    return passed, dict(row), summary


def check_tombstone_consistency(wh):
    row = wh.scalar(TOMBSTONE_SQL)
    passed = row["leaked"] == 0
    evidence = dict(row)
    if not passed:
        evidence["leak_pct"] = _pct(row["leaked"], row["total_households"])
        evidence["samples"] = wh.query(TOMBSTONE_EVIDENCE_SQL)
    summary = (
        f"all {row['tombstoned']:,} tombstoned households are inactive"
        if passed
        else f"{row['leaked']:,} deleted households are still flagged active"
    )
    return passed, evidence, summary


def check_household_grain(wh):
    row = wh.scalar(HOUSEHOLD_GRAIN_SQL)
    dupes = row["row_count"] - row["distinct_key"]
    passed = dupes == 0
    summary = (
        "household_id is unique"
        if passed
        else f"{dupes:,} duplicate household rows"
    )
    return passed, dict(row), summary


CHECKS = [
    {
        "check_id": "fct_visit.unique_grain",
        "contract": 1,
        "table": "fct_visit",
        "severity": "high",
        "title": "Visit grain is unique",
        "fn": check_visit_grain,
    },
    {
        "check_id": "fct_visit.freshness",
        "contract": 2,
        "table": "fct_visit",
        "severity": "medium",
        "title": "Visits are fresh",
        "fn": check_visit_freshness,
    },
    {
        "check_id": "fct_visit.source_liveness",
        "contract": 3,
        "table": "fct_visit",
        "severity": "high",
        "title": "Every source is still reporting",
        "fn": check_source_liveness,
    },
    {
        "check_id": "fct_visit.unit_coverage",
        "contract": 4,
        "table": "fct_visit",
        "severity": "high",
        "title": "Every reporting unit is still reporting",
        "fn": check_unit_coverage,
    },
    {
        "check_id": "fct_visit.referential_integrity",
        "contract": 5,
        "table": "fct_visit",
        "severity": "high",
        "title": "Visit keys resolve to dimensions",
        "fn": check_referential_integrity,
    },
    {
        "check_id": "fct_household.tombstone_consistency",
        "contract": 6,
        "table": "fct_household",
        "severity": "high",
        "title": "Deleted households are inactive",
        "fn": check_tombstone_consistency,
    },
    {
        "check_id": "fct_household.unique_grain",
        "contract": 7,
        "table": "fct_household",
        "severity": "high",
        "title": "Household grain is unique",
        "fn": check_household_grain,
    },
]


def run_one(wh, spec):
    """Run one check, converting any failure into an error result.

    A check that raises must not stop the sweep. The run continues and the
    error is reported alongside the passes and fails.
    """
    started = time.monotonic()
    try:
        passed, evidence, summary = spec["fn"](wh)
        error = None
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        passed, evidence, summary = False, {}, ""
        error = f"{type(exc).__name__}: {exc}"
        evidence = {"traceback": traceback.format_exc(limit=3)}

    return CheckResult(
        check_id=spec["check_id"],
        contract=spec["contract"],
        table=spec["table"],
        severity=spec["severity"],
        title=spec["title"],
        passed=passed,
        evidence=evidence,
        summary=summary,
        error=error,
        duration_ms=int((time.monotonic() - started) * 1000),
        bytes_billed=getattr(wh, "last_bytes_billed", 0),
    )


def run_all(wh, parallel=True):
    """Run every check.

    Checks are independent reads, so they run concurrently. Sequentially the
    sweep takes about 17 seconds, which is too slow for a page a person is
    waiting on. Each check gets its own Warehouse so the BigQuery client is
    not shared across threads.
    """
    if not parallel:
        return [run_one(wh, spec) for spec in CHECKS]

    def isolated(spec):
        from .bq import Warehouse

        return run_one(
            Warehouse(project=wh.project, dataset=wh.dataset,
                      location=wh.location),
            spec,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        return list(pool.map(isolated, CHECKS))
