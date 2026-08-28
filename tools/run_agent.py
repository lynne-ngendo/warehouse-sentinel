"""Run the full sweep: check every contract, then diagnose what failed.

  python tools/run_agent.py
  python tools/run_agent.py --json

Detection is deterministic and always runs. The agent is invoked only for
contracts that already failed, so a model outage costs you diagnosis, not
detection.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentinel import checks  # noqa: E402
from sentinel.agent import diagnose, verify_reproducing_query  # noqa: E402
from sentinel.bq import Warehouse  # noqa: E402
from sentinel.sink import GitHubIssueSink  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dataset", default=None)
    parser.add_argument(
        "--file-issues", action="store_true",
        help="file each finding as a GitHub issue",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="render the issue that would be filed, without filing it",
    )
    args = parser.parse_args()

    wh = Warehouse(dataset=args.dataset)
    sink = GitHubIssueSink() if (args.file_issues or args.dry_run) else None
    results = checks.run_all(wh)
    failed = [r for r in results if r.status != "pass"]

    report = {
        "dataset": f"{wh.project}.{wh.dataset}",
        "contracts_checked": len(results),
        "contracts_failed": len(failed),
        "findings": [],
    }

    for result in failed:
        try:
            diagnosis, trace = diagnose(result, wh)
        except Exception as exc:  # noqa: BLE001 - one failure is not fatal
            report["findings"].append({
                "check_id": result.check_id,
                "summary": result.summary,
                "diagnosis": None,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            })
            continue

        verification = (
            verify_reproducing_query(wh, diagnosis) if diagnosis else None
        )
        filed = None
        if sink:
            filed = sink.file(
                result, diagnosis, verification, trace["model"],
                dry_run=args.dry_run,
            )

        report["findings"].append({
            "check_id": result.check_id,
            "severity": result.severity,
            "summary": result.summary,
            "diagnosis": diagnosis.model_dump() if diagnosis else None,
            "verification": verification,
            "filed": filed,
            "tool_calls": len(trace["tool_calls"]),
            "model": trace["model"],
        })

    if args.json:
        print(json.dumps(report, default=str, indent=2))
        return 1 if failed else 0

    print(f"dataset: {report['dataset']}")
    print(
        f"{report['contracts_checked'] - report['contracts_failed']}"
        f"/{report['contracts_checked']} contracts hold\n"
    )
    for f in report["findings"]:
        d = f["diagnosis"]
        print(f"FAIL  {f['check_id']}  {f['summary']}")
        if not d:
            print(f"      diagnosis unavailable: {f.get('error')}")
            continue
        v = f["verification"]
        mark = (
            f"verified, {v['row_count']} rows" if v and v["ran"]
            else f"QUERY FAILED: {v['error'] if v else 'none'}"
        )
        print(f"      {d['failure_mode']} ({d['confidence']} confidence)")
        print(f"      {d['title']}")
        print(f"      repro query {mark}, {f['tool_calls']} tool calls")
        if f.get("filed"):
            filed = f["filed"]
            where = filed.get("url") or filed.get("reason") or ""
            print(f"      issue {filed['action']}: {where}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
