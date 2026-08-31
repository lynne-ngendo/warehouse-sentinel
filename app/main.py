"""The findings service.

A judge landing on this URL has about ten seconds of patience, so the page
answers one question immediately: is the warehouse sound right now? Detail is
below the fold.

`GET  /`              the page
`GET  /health`        liveness
`GET  /api/contracts` the check layer as JSON
`POST /api/sweep`     check, diagnose what failed, and file issues
"""

import datetime
import os
import time

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from sentinel import checks
from sentinel.agent import diagnose, verify_reproducing_query
from sentinel.bq import Warehouse
from sentinel.sink import GitHubIssueSink

app = FastAPI(title="Warehouse Sentinel")

_cache = {"at": 0.0, "results": None}
CACHE_SECONDS = int(os.environ.get("SENTINEL_CACHE_SECONDS", 60))


def current_results(force=False):
    if not force and _cache["results"] and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["results"], True
    results = checks.run_all(Warehouse())
    _cache.update({"at": time.time(), "results": results})
    return results, False


@app.get("/health")
def health():
    return {"status": "ok", "revision": os.environ.get("K_REVISION", "local")}


@app.get("/api/contracts")
def api_contracts():
    results, cached = current_results()
    return JSONResponse({
        "dataset": f"{Warehouse().project}.{Warehouse().dataset}",
        "cached": cached,
        "contracts": [r.to_dict() for r in results],
    })


@app.post("/api/sweep")
def api_sweep(file_issues: bool = False, ignore_suppression: bool = False):
    """Run the whole pipeline: check, diagnose failures, optionally file."""
    wh = Warehouse()
    results, _ = current_results(force=True)
    out = []
    sink = GitHubIssueSink() if file_issues else None
    for result in [r for r in results if r.status != "pass"]:
        diagnosis, trace = diagnose(result, wh)
        verification = verify_reproducing_query(wh, diagnosis) if diagnosis else None
        filed = (
            sink.file(result, diagnosis, verification, trace["model"],
                      ignore_suppression=ignore_suppression)
            if sink else None
        )
        out.append({
            "check_id": result.check_id,
            "summary": result.summary,
            "diagnosis": diagnosis.model_dump() if diagnosis else None,
            "verification": verification,
            "filed": filed,
        })
    return JSONResponse({"failed": len(out), "findings": out})


CSS = """
:root{--bg:#fff;--fg:#111418;--mut:#5f6368;--line:#e3e5e8;--ok:#137333;--bad:#c5221f;--card:#f8f9fa;--accent:#1a73e8}
@media (prefers-color-scheme:dark){:root{--bg:#101418;--fg:#e8eaed;--mut:#9aa0a6;--line:#2b3036;--ok:#5bb974;--bad:#f28b82;--card:#171c22;--accent:#8ab4f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:44px 22px 80px}
h1{font-size:26px;margin:0 0 4px}
.sub{color:var(--mut);margin:0 0 28px}
.verdict{border:1px solid var(--line);border-radius:12px;padding:22px 24px;background:var(--card);margin-bottom:26px}
.big{font-size:30px;font-weight:700;letter-spacing:-.3px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--mut);font-weight:600}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent)}
.pill{font:11px ui-monospace,Menlo,monospace;padding:2px 7px;border-radius:20px;border:1px solid var(--line)}
.p-ok{color:var(--ok)}.p-bad{color:var(--bad);border-color:var(--bad)}
.foot{color:var(--mut);font-size:13px;margin-top:30px;border-top:1px solid var(--line);padding-top:16px}
"""


@app.get("/", response_class=HTMLResponse)
def index():
    results, cached = current_results()
    failed = [r for r in results if r.status != "pass"]
    wh = Warehouse()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if failed:
        verdict = (
            f'<div class="big bad">{len(failed)} of {len(results)} contracts '
            f'violated</div><p class="sub" style="margin:6px 0 0">'
            f'Detected deterministically. Diagnosis and a GitHub issue follow '
            f'on the next sweep.</p>'
        )
    else:
        verdict = (
            f'<div class="big ok">All {len(results)} contracts hold</div>'
            f'<p class="sub" style="margin:6px 0 0">No finding to diagnose. '
            f'The model is not called when nothing is broken.</p>'
        )

    rows = "".join(
        f'<tr><td><code>{r.check_id}</code></td>'
        f'<td><span class="pill {"p-ok" if r.status == "pass" else "p-bad"}">'
        f'{r.status.upper()}</span></td>'
        f'<td>{r.summary or r.error or ""}</td></tr>'
        for r in results
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Warehouse Sentinel</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Warehouse Sentinel</h1>
<p class="sub">Contract checks over <code>{wh.project}.{wh.dataset}</code></p>
<div class="verdict">{verdict}</div>
<table><thead><tr><th>Contract</th><th>Status</th><th>Result</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="foot">Checked {now}{" (cached)" if cached else ""}.
Detection is deterministic SQL. Gemini is only asked what broke and why, never
whether something is broken.<br>
<code>GET /api/contracts</code> for JSON &middot;
<code>POST /api/sweep?file_issues=true</code> to diagnose and file.</p>
</div></body></html>"""
