"""Generate a synthetic community health warehouse, optionally with faults.

The warehouse models household visits made by community health workers. A clean
run satisfies every contract in `contracts.md`. Each `--fault` injects one
realistic pipeline failure on top of the same baseline, so a before-and-after
comparison isolates exactly one variable.

Faults:

  duplication    A re-ingest emits three copies of every visit in a 3 day
                 window. `visit_id` is no longer unique, so counts inflate.
  stall          The `sms` source stops producing rows partway through. Volume
                 dips about 8 percent, which is inside normal variation.
  soft_delete    Household tombstones are written to `deleted_at` but
                 `is_active` is never flipped, so deleted households still
                 count as active.
  coverage_gap   Six units stop reporting entirely. Remaining units are scaled
                 up so the daily total stays flat, which is what makes this
                 invisible to a threshold alert.

Usage:
  python3 generate_warehouse.py --out data
  python3 generate_warehouse.py --out data --fault coverage_gap
"""

import argparse
import json
import os
import random
from datetime import date, datetime, time, timedelta

SEED = 20260827
DAYS = 90
END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=DAYS - 1)

N_UNITS = 40
WORKERS_PER_UNIT = 5
HOUSEHOLDS_PER_UNIT = 500

SMS_SHARE = 0.08
STALL_START = END_DATE - timedelta(days=21)
GAP_START = END_DATE - timedelta(days=28)
GAP_UNIT_COUNT = 6
DUP_WINDOW_START = END_DATE - timedelta(days=14)
DUP_WINDOW_DAYS = 3
DUP_COPIES = 3


def configure(end_date):
    """Pin the generation window.

    Defaults to today so the freshness contract passes on clean data. Pin it
    with --end-date to reproduce an earlier run exactly.
    """
    global END_DATE, START_DATE, STALL_START, GAP_START, DUP_WINDOW_START
    END_DATE = end_date
    START_DATE = END_DATE - timedelta(days=DAYS - 1)
    STALL_START = END_DATE - timedelta(days=21)
    GAP_START = END_DATE - timedelta(days=28)
    DUP_WINDOW_START = END_DATE - timedelta(days=14)

PLACE_WORDS = [
    "Acacia", "Baobab", "Cedar", "Delta", "Elmwood", "Fernvale", "Greenhill",
    "Harbour", "Ironstone", "Juniper", "Kestrel", "Larkspur", "Meadow",
    "Northgate", "Orchard", "Pinecrest", "Quarry", "Riverbend", "Stonebridge",
    "Thornhill", "Upland", "Verdant", "Westbrook", "Yarrow",
]
GIVEN_NAMES = [
    "Amara", "Bilal", "Chen", "Dalia", "Eero", "Farida", "Goran", "Hana",
    "Ivo", "Jamila", "Kwame", "Lucia", "Mateo", "Nadia", "Omar", "Priya",
    "Rania", "Samir", "Tomas", "Ute", "Viktor", "Wanjiku", "Yusuf", "Zara",
]
FAMILY_NAMES = [
    "Adeyemi", "Baros", "Costa", "Duarte", "Espinoza", "Farah", "Gupta",
    "Haddad", "Ibrahim", "Jensen", "Kaur", "Lindqvist", "Moreau", "Novak",
    "Okafor", "Pereira", "Quintero", "Rossi", "Silva", "Tanaka",
]
VISIT_TYPES = ["routine", "follow_up", "referral", "registration"]
OUTCOMES = ["completed", "not_home", "refused", "partial"]


def daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_dimensions(rng):
    units, workers, households = [], [], []
    for i in range(N_UNITS):
        unit_id = f"U{i + 1:03d}"
        name = f"{PLACE_WORDS[i % len(PLACE_WORDS)]} {i // len(PLACE_WORDS) + 1}"
        subcounty = f"Subcounty {i // 4 + 1:02d}"
        county = f"County {i // 14 + 1}"
        units.append({
            "unit_id": unit_id,
            "unit_name": name,
            "subcounty": subcounty,
            "county": county,
            "base_rate": round(rng.uniform(20, 60), 2),
        })
        for w in range(WORKERS_PER_UNIT):
            workers.append({
                "worker_id": f"{unit_id}-W{w + 1:02d}",
                "worker_name": (
                    f"{rng.choice(GIVEN_NAMES)} {rng.choice(FAMILY_NAMES)}"
                ),
                "unit_id": unit_id,
                "hired_date": (
                    START_DATE - timedelta(days=rng.randint(30, 900))
                ).isoformat(),
            })
        for h in range(HOUSEHOLDS_PER_UNIT):
            households.append({
                "household_id": f"{unit_id}-H{h + 1:04d}",
                "unit_id": unit_id,
                "registered_date": (
                    START_DATE - timedelta(days=rng.randint(0, 1200))
                ).isoformat(),
                "member_count": rng.randint(1, 11),
                "deleted_at": None,
                "is_active": True,
            })
    return units, workers, households


def apply_tombstones(households, rng, leak):
    """Mark 5 percent of households deleted.

    Clean data flips `is_active` to False. The `soft_delete` fault writes the
    tombstone but leaves `is_active` True, which is the contract violation.
    """
    for hh in households:
        if rng.random() < 0.05:
            deleted = datetime.combine(
                START_DATE + timedelta(days=rng.randint(0, DAYS - 1)),
                time(rng.randint(8, 17), rng.randint(0, 59)),
            )
            hh["deleted_at"] = iso(deleted)
            hh["is_active"] = True if leak else False


def daily_unit_volume(units, day, rng, gap_units):
    """Expected visits per unit for one day, with the gap redistributed."""
    weekday_factor = 0.3 if day.weekday() >= 5 else 1.0
    expected = {}
    for unit in units:
        noise = rng.gauss(1.0, 0.12)
        expected[unit["unit_id"]] = max(
            0.0, unit["base_rate"] * weekday_factor * noise
        )

    if not gap_units:
        return expected

    missing = sum(expected[u] for u in gap_units)
    remaining = [u for u in expected if u not in gap_units]
    remaining_total = sum(expected[u] for u in remaining)
    for unit_id in gap_units:
        expected[unit_id] = 0.0
    if remaining_total > 0:
        # Push the absent units' volume onto everyone else so the daily total
        # holds. This is what hides the gap from a threshold alert.
        scale = 1.0 + (missing / remaining_total)
        for unit_id in remaining:
            expected[unit_id] *= scale
    return expected


def generate_visits(units, workers, households, rng, faults):
    by_unit_workers = {}
    for w in workers:
        by_unit_workers.setdefault(w["unit_id"], []).append(w["worker_id"])
    by_unit_households = {}
    for hh in households:
        by_unit_households.setdefault(hh["unit_id"], []).append(hh["household_id"])

    gap_units = set()
    if "coverage_gap" in faults:
        gap_units = {u["unit_id"] for u in units[:GAP_UNIT_COUNT]}

    visits = []
    counter = 0
    for day in daterange(START_DATE, END_DATE):
        active_gap = gap_units if (
            "coverage_gap" in faults and day >= GAP_START
        ) else set()
        expected = daily_unit_volume(units, day, rng, active_gap)
        for unit in units:
            unit_id = unit["unit_id"]
            n = int(round(expected[unit_id]))
            for _ in range(n):
                source = "sms" if rng.random() < SMS_SHARE else "mobile"
                if (
                    "stall" in faults
                    and source == "sms"
                    and day >= STALL_START
                ):
                    continue
                counter += 1
                ts = datetime.combine(
                    day, time(rng.randint(7, 18), rng.randint(0, 59))
                )
                visits.append({
                    "visit_id": f"V{counter:08d}",
                    "visit_date": day.isoformat(),
                    "visit_ts": iso(ts),
                    "unit_id": unit_id,
                    "worker_id": rng.choice(by_unit_workers[unit_id]),
                    "household_id": rng.choice(by_unit_households[unit_id]),
                    "visit_type": rng.choice(VISIT_TYPES),
                    "outcome": rng.choice(OUTCOMES),
                    "source_system": source,
                    "ingested_at": iso(ts + timedelta(hours=rng.randint(1, 6))),
                    "source_batch": f"batch-{day.isoformat()}",
                })

    if "duplication" in faults:
        window_end = DUP_WINDOW_START + timedelta(days=DUP_WINDOW_DAYS - 1)
        window = [
            v for v in visits
            if DUP_WINDOW_START.isoformat() <= v["visit_date"] <= window_end.isoformat()
        ]
        for copy_index in range(1, DUP_COPIES):
            for v in window:
                dup = dict(v)
                dup["source_batch"] = f"{v['source_batch']}-retry{copy_index}"
                dup["ingested_at"] = iso(
                    datetime.strptime(v["ingested_at"], "%Y-%m-%d %H:%M:%S")
                    + timedelta(days=copy_index)
                )
                visits.append(dup)

    return visits


def write_ndjson(path, rows, drop_keys=()):
    with open(path, "w") as fh:
        for row in rows:
            out = {k: v for k, v in row.items() if k not in drop_keys}
            fh.write(json.dumps(out) + "\n")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data", help="output directory")
    parser.add_argument(
        "--fault",
        action="append",
        default=[],
        choices=["duplication", "stall", "soft_delete", "coverage_gap"],
        help="inject a fault; repeatable",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="last day of the window, ISO format; defaults to today",
    )
    args = parser.parse_args()

    configure(
        date.fromisoformat(args.end_date) if args.end_date else date.today()
    )

    faults = set(args.fault)
    rng = random.Random(SEED)
    os.makedirs(args.out, exist_ok=True)

    units, workers, households = build_dimensions(rng)
    apply_tombstones(households, rng, leak="soft_delete" in faults)
    visits = generate_visits(units, workers, households, rng, faults)

    counts = {
        "dim_unit": write_ndjson(
            f"{args.out}/dim_unit.ndjson", units, drop_keys=("base_rate",)
        ),
        "dim_worker": write_ndjson(f"{args.out}/dim_worker.ndjson", workers),
        "fct_household": write_ndjson(
            f"{args.out}/fct_household.ndjson", households
        ),
        "fct_visit": write_ndjson(f"{args.out}/fct_visit.ndjson", visits),
    }

    print(f"faults: {sorted(faults) or ['none']}")
    print(f"window: {START_DATE} to {END_DATE}")
    for table, n in counts.items():
        print(f"  {table:<16} {n:>8,} rows")


if __name__ == "__main__":
    main()
