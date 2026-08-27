#!/bin/sh
# Load one generated variant into a BigQuery dataset, replacing what is there.
#
#   ./tools/load_bigquery.sh clean
#   ./tools/load_bigquery.sh coverage_gap
#
# The dataset is always `sentinel`, so the agent reads one fixed location and
# the variant is swapped underneath it.
set -e

VARIANT="${1:-clean}"
PROJECT="${PROJECT:-warehouse-sentinel-2608}"
DATASET="${DATASET:-sentinel}"
LOCATION="${LOCATION:-US}"

export PATH="$HOME/google-cloud-sdk/bin:$PATH"
export CLOUDSDK_PYTHON="$HOME/.local/bin/python3.11"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data/$VARIANT"
[ -d "$DATA" ] || { echo "no generated data at $DATA"; exit 1; }

bq --location="$LOCATION" --project_id="$PROJECT" mk -f --dataset \
  --description "Synthetic warehouse for warehouse-sentinel" \
  "$PROJECT:$DATASET" >/dev/null

for TABLE in dim_unit dim_worker fct_household fct_visit; do
  bq --location="$LOCATION" --project_id="$PROJECT" load \
    --source_format=NEWLINE_DELIMITED_JSON \
    --replace \
    "$DATASET.$TABLE" \
    "$DATA/$TABLE.ndjson" \
    "$ROOT/tools/schemas/$TABLE.json" >/dev/null
  echo "loaded $TABLE from $VARIANT"
done
