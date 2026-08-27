"""Run every contract check against the warehouse and print the results.

  python tools/run_checks.py
  python tools/run_checks.py --json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentinel import checks  # noqa: E402
from sentinel.bq import Warehouse  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args()

    wh = Warehouse(dataset=args.dataset)
    results = checks.run_all(wh)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], default=str, indent=2))
        return 1 if any(r.status != "pass" for r in results) else 0

    icons = {"pass": "PASS", "fail": "FAIL", "error": "ERR "}
    print(f"dataset: {wh.project}.{wh.dataset}\n")
    for r in results:
        print(f"{icons[r.status]}  {r.check_id:<38} {r.summary or r.error}")
    failed = [r for r in results if r.status != "pass"]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} contracts hold, "
        f"{sum(r.duration_ms for r in results)} ms total"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
