# Warehouse Sentinel

An autonomous agent that catches the data warehouse failures threshold alerts
miss, diagnoses each one, and files a GitHub issue with a reproducing query
attached.

Built for the All Things Agentic Hackathon (Taskmaster track).

## The problem

Threshold alerts catch loud failures. The expensive ones are quiet. When a
subset of reporting units stops sending data and the remaining units absorb the
work, the daily total never moves and every dashboard stays green.

Warehouse Sentinel declares table contracts, checks them in SQL, and hands the
evidence to Gemini for diagnosis.

## Architecture

| Layer | What it does | Technology |
| --- | --- | --- |
| Detection | Turns each contract into SQL and decides pass or fail | BigQuery, read-only, cost-capped |
| Diagnosis | Classifies the failure, writes the reproducing query, drafts the ticket | Gemini 3.5 Flash on Vertex AI |
| Orchestration | Runs the sweep, calls the tools, bounds the loop | Agent Development Kit |
| State | Run history and idempotency keys | Firestore |
| Action | Files the ticket | GitHub Issues API |
| Runtime | Scheduled sweep and findings service | Cloud Run, Cloud Scheduler |

**The model never decides whether something is broken.** Detection is
deterministic SQL. Gemini is only asked what broke and why.

## Contracts

See [contracts.md](contracts.md) for the seven properties the warehouse must
hold and which fault violates each.

## Reproducible testing

Every step below has been run end to end. `requirements.txt` is verified to
install from clean into a fresh virtualenv.

### 1. Prerequisites

- Python 3.11 or later
- A Google Cloud project with billing enabled, and the BigQuery, Vertex AI, and
  Cloud Run APIs turned on
- `gcloud` authenticated, including application default credentials:

```sh
gcloud auth login
gcloud auth application-default login
pip install -r requirements.txt
```

### 2. Configure

```sh
export SENTINEL_PROJECT=your-project-id
export SENTINEL_DATASET=sentinel
```

### 3. Generate the synthetic warehouse

```sh
python3 tools/generate_warehouse.py --out data/clean
./tools/load_bigquery.sh clean
```

The generator is seeded, so runs reproduce exactly. Pin the window with
`--end-date` to reproduce an earlier run.

### 4. Run the check layer

```sh
python tools/run_checks.py
```

All seven contracts should hold on clean data.

### 4b. Run the full sweep, detection plus diagnosis

```sh
python tools/run_agent.py
```

Gemini 3.x is served from the `global` Vertex location. Regional endpoints
return 404 for every 3.x model, so leave `SENTINEL_VERTEX_LOCATION` alone
unless you know otherwise.

### 5. Inject a fault and watch it get caught

```sh
python3 tools/generate_warehouse.py --out data/coverage_gap --fault coverage_gap
./tools/load_bigquery.sh coverage_gap
python tools/run_checks.py
```

Available faults: `duplication`, `stall`, `soft_delete`, `coverage_gap`.

## Detection matrix

Each fault is caught by exactly one check, and clean data produces no false
positives.

| Variant | Check that fails |
| --- | --- |
| clean | none |
| duplication | `fct_visit.unique_grain` |
| stall | `fct_visit.source_liveness` |
| soft_delete | `fct_household.tombstone_consistency` |
| coverage_gap | `fct_visit.unit_coverage` |

## Data sources

No external or third-party data. The warehouse is synthetically generated with
a fixed seed, so the faults are reproducible for demo and judging.

The checks run against any BigQuery dataset that conforms to the declared
schema in [contracts.md](contracts.md). Target project and dataset are
configurable through `SENTINEL_PROJECT` and `SENTINEL_DATASET`. The generator
exists to make the faults reproducible, not because the checks are tied to it.

## Security

The Cloud Run service account authenticates to Vertex AI directly, so there is
no model API key anywhere in this project. The only secret in Secret Manager is
the GitHub token used to file issues.

Every warehouse query is read-only and capped with `maximum_bytes_billed`, so a
runaway scan is rejected by BigQuery before it costs anything. Model-authored
SQL runs through a separate path that accepts a single `SELECT` or `WITH`,
refuses write keywords, and refuses multiple statements.

## License

MIT
