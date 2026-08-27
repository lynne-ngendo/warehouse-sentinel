"""Runtime configuration, read from the environment with safe defaults."""

import os

PROJECT = os.environ.get("SENTINEL_PROJECT", "warehouse-sentinel-2608")
DATASET = os.environ.get("SENTINEL_DATASET", "sentinel")
LOCATION = os.environ.get("SENTINEL_LOCATION", "US")

# Hard ceiling on a single check query. A check that would scan more than this
# fails loudly rather than running up a bill.
MAX_BYTES_BILLED = int(os.environ.get("SENTINEL_MAX_BYTES", 2 * 1024**3))

# Windows used by the coverage and liveness contracts, in days.
TRAILING_DAYS = int(os.environ.get("SENTINEL_TRAILING_DAYS", 14))
BASELINE_DAYS = int(os.environ.get("SENTINEL_BASELINE_DAYS", 56))
FRESHNESS_MAX_LAG_DAYS = int(os.environ.get("SENTINEL_FRESHNESS_LAG", 1))
LIVENESS_MAX_LAG_DAYS = int(os.environ.get("SENTINEL_LIVENESS_LAG", 3))
