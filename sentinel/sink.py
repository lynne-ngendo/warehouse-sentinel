"""File findings as GitHub issues.

This is the action half of the agent. Detection and diagnosis are worth little
if the result stops at stdout, so a finding ends its life as an issue in the
repository that owns the pipeline, with the reproducing query attached.

Idempotency is deliberate. A sweep that runs every six hours must not file the
same issue four times a day, so every issue carries a fingerprint and an open
issue with a matching fingerprint suppresses a new one. A closed issue does
not: if the problem comes back after someone closed it, that is news.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

API = "https://api.github.com"
MARKER = "warehouse-sentinel-fingerprint"


def _identity(result) -> str:
    """A stable identifier for the thing that is broken.

    Deliberately excludes volatile numbers. A coverage gap that grows from six
    units to seven is the same incident, not a new one.
    """
    ev = result.evidence or {}
    for key in ("missing_units", "stale_sources", "worst_dates"):
        value = ev.get(key)
        if value:
            if key == "stale_sources":
                return ",".join(sorted(str(v.get("source_system")) for v in value))
            if key == "worst_dates":
                return ",".join(sorted(str(v.get("visit_date")) for v in value))
            return ",".join(sorted(str(v) for v in value))
    return result.table


def fingerprint(result) -> str:
    raw = f"{result.check_id}|{_identity(result)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def resolve_token() -> Optional[str]:
    """Token from the environment, or from Secret Manager on Cloud Run."""
    token = os.environ.get("SENTINEL_GITHUB_TOKEN")
    if token:
        return token.strip()
    secret = os.environ.get("SENTINEL_GITHUB_TOKEN_SECRET")
    if not secret:
        return None
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=secret)
        return response.payload.data.decode().strip()
    except Exception:  # noqa: BLE001 - absence is reported by the caller
        return None


def issue_body(result, diagnosis, verification, model: str) -> str:
    d = diagnosis
    causes = "\n".join(f"{i}. {c}" for i, c in enumerate(d.likely_causes, 1))
    if verification and verification.get("ran"):
        verdict = (
            f"Verified before filing: the query above ran read-only and "
            f"returned {verification['row_count']} rows."
        )
    else:
        error = (verification or {}).get("error", "not run")
        verdict = f"The query above did not execute cleanly: {error}"

    evidence = json.dumps(result.evidence, default=str, indent=2)
    if len(evidence) > 4000:
        evidence = evidence[:4000] + "\n... truncated ..."

    return f"""\
## What broke

{d.what_broke}

## Why nothing alerted

{d.why_it_is_invisible}

## Reproduce it

```sql
{d.reproducing_query.strip()}
```

{verdict}

## Likely causes

{causes}

## How this was found

A deterministic contract check failed. Detection is SQL, not a model, so this
issue is a fact about the data rather than an opinion about it.

| | |
| --- | --- |
| Contract | `{result.check_id}` (contract {result.contract}) |
| Table | `{result.table}` |
| Severity | {result.severity} |
| Check result | {result.summary} |
| Classification | `{d.failure_mode}`, {d.confidence} confidence |
| Suggested owner | {d.suggested_owner} |
| Diagnosed by | {model} on Vertex AI |

<details>
<summary>Evidence from the check layer</summary>

```json
{evidence}
```

</details>

<!-- {MARKER}: {fingerprint(result)} -->
"""


class GitHubIssueSink:
    """Creates one issue per finding, at most once while it stays open."""

    def __init__(self, repo: Optional[str] = None, token: Optional[str] = None):
        self.repo = repo or os.environ.get(
            "SENTINEL_GITHUB_REPO", "lynne-ngendo/warehouse-sentinel"
        )
        self.token = token or resolve_token()

    def _request(self, method: str, path: str, payload=None):
        req = urllib.request.Request(
            f"{API}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "warehouse-sentinel",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())

    def open_issues(self) -> List[Dict[str, Any]]:
        return self._request(
            "GET", f"/repos/{self.repo}/issues?state=open&per_page=100"
        )

    def existing(self, fp: str) -> Optional[Dict[str, Any]]:
        for issue in self.open_issues():
            if fp in (issue.get("body") or ""):
                return issue
        return None

    def file(self, result, diagnosis, verification, model, dry_run=False):
        """File one finding. Returns what happened and why."""
        if not diagnosis:
            return {"action": "skipped", "reason": "no diagnosis"}
        fp = fingerprint(result)
        body = issue_body(result, diagnosis, verification, model)

        if dry_run:
            return {"action": "dry_run", "fingerprint": fp,
                    "title": diagnosis.title, "body": body}
        if not self.token:
            return {"action": "failed", "reason": "no GitHub token available"}

        try:
            duplicate = self.existing(fp)
        except urllib.error.HTTPError as exc:
            return {"action": "failed", "reason": f"HTTP {exc.code} listing issues"}

        if duplicate:
            return {
                "action": "suppressed",
                "reason": "an open issue already reports this",
                "url": duplicate["html_url"],
                "number": duplicate["number"],
                "fingerprint": fp,
            }

        try:
            created = self._request(
                "POST",
                f"/repos/{self.repo}/issues",
                {
                    "title": diagnosis.title,
                    "body": body,
                    "labels": ["warehouse-sentinel", f"severity:{result.severity}"],
                },
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:200]
            return {"action": "failed", "reason": f"HTTP {exc.code}: {detail}"}

        return {
            "action": "created",
            "url": created["html_url"],
            "number": created["number"],
            "fingerprint": fp,
        }
