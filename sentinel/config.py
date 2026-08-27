"""Runtime configuration, read from the environment with safe defaults."""

import os

PROJECT = os.environ.get("SENTINEL_PROJECT", "warehouse-sentinel-2608")
DATASET = os.environ.get("SENTINEL_DATASET", "sentinel")
LOCATION = os.environ.get("SENTINEL_LOCATION", "US")

# Vertex AI. Gemini 3.x models are served from the `global` location only;
# every 3.x model returns 404 in us-central1 on this project, verified
# 2026-08-28. Do not "fix" this to a regional endpoint.
VERTEX_LOCATION = os.environ.get("SENTINEL_VERTEX_LOCATION", "global")
MODEL = os.environ.get("SENTINEL_MODEL", "gemini-3.5-flash")

# Bounds on the agent loop. A diagnosis that needs more than this is a bug,
# not a hard problem.
MAX_TOOL_CALLS = int(os.environ.get("SENTINEL_MAX_TOOL_CALLS", 8))
AGENT_TIMEOUT_SECONDS = int(os.environ.get("SENTINEL_AGENT_TIMEOUT", 120))

# Hard ceiling on a single check query. A check that would scan more than this
# fails loudly rather than running up a bill.
MAX_BYTES_BILLED = int(os.environ.get("SENTINEL_MAX_BYTES", 2 * 1024**3))

# Windows used by the coverage and liveness contracts, in days.
TRAILING_DAYS = int(os.environ.get("SENTINEL_TRAILING_DAYS", 14))
BASELINE_DAYS = int(os.environ.get("SENTINEL_BASELINE_DAYS", 56))
FRESHNESS_MAX_LAG_DAYS = int(os.environ.get("SENTINEL_FRESHNESS_LAG", 1))
LIVENESS_MAX_LAG_DAYS = int(os.environ.get("SENTINEL_LIVENESS_LAG", 3))
