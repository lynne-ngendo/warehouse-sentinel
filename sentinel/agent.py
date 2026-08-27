"""The diagnosis agent.

The agent is given one failed contract and its evidence, and is asked what
broke and why. It never decides *whether* something is broken: that has already
been settled deterministically by the check layer.

Structure is enforced by making the final action a tool call. `submit_diagnosis`
takes exactly the fields of `Diagnosis`, so a malformed answer fails validation
at the tool boundary instead of reaching the ticket writer.
"""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import config
from .bq import Warehouse
from .diagnosis import Diagnosis

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", config.VERTEX_LOCATION)

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|truncate|grant|revoke|"
    r"call|export|load)\b",
    re.IGNORECASE,
)

INSTRUCTION = """\
You diagnose data warehouse failures. A deterministic check layer has already
decided that a contract is violated. That decision is final and is not yours to
revisit. Your job is to explain what broke, why it stayed invisible, and how to
reproduce it.

Work in this order:

1. Read the evidence you are given. It is the output of real SQL, not a summary.
2. Use `describe_table` and `profile_daily` if you need shape or history that
   the evidence does not contain.
3. Use `run_query` to test a specific hypothesis. Read-only SELECT only.
4. Call `submit_diagnosis` exactly once, as your final action.

Rules:

- Ground every claim in a number you have actually seen. Do not estimate.
- The reproducing query must run as written against the tables named in the
  evidence, and must show the problem to someone who has not read your text.
- If the evidence does not distinguish between causes, say so and set
  confidence to low. A low-confidence honest answer is worth more than a
  confident guess.
- Keep tool calls to a minimum. You should rarely need more than three.
"""


class DiagnosisSession:
    """Holds the tools and captures the diagnosis for one finding."""

    def __init__(self, warehouse: Optional[Warehouse] = None):
        self.wh = warehouse or Warehouse()
        self.result: Optional[Diagnosis] = None
        self.tool_calls: List[Dict[str, Any]] = []
        self.qualified = f"{self.wh.project}.{self.wh.dataset}"

    # --- tools ---------------------------------------------------------

    def _over_budget(self) -> bool:
        return len(self.tool_calls) > config.MAX_TOOL_CALLS

    def run_query(self, sql: str) -> str:
        """Run one read-only BigQuery SELECT and return up to 20 rows as JSON.

        Args:
            sql: A single SELECT or WITH statement. Fully qualify tables as
                `project.dataset.table`. Any statement that writes is refused.
        """
        self.tool_calls.append({"tool": "run_query", "sql": sql[:400]})
        if self._over_budget():
            return "error: tool call budget exhausted; call submit_diagnosis now"
        stripped = sql.strip().rstrip(";").strip()
        if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
            return "error: only SELECT or WITH statements are allowed"
        if FORBIDDEN.search(stripped):
            return "error: statement contains a write keyword and was refused"
        if ";" in stripped:
            return "error: only one statement per call"
        try:
            rows = self.wh.query_raw(stripped)
        except Exception as exc:  # noqa: BLE001 - returned to the model
            return f"error: {type(exc).__name__}: {str(exc)[:300]}"
        return json.dumps(rows[:20], default=str)[:6000]

    def describe_table(self, table: str) -> str:
        """Return column names and types for one table in the warehouse.

        Args:
            table: Bare table name, for example `fct_visit`.
        """
        self.tool_calls.append({"tool": "describe_table", "table": table})
        if self._over_budget():
            return "error: tool call budget exhausted; call submit_diagnosis now"
        safe = re.sub(r"[^A-Za-z0-9_]", "", table)
        sql = (
            "SELECT column_name, data_type, is_nullable "
            "FROM {dataset}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE table_name = '{safe}' ORDER BY ordinal_position"
        )
        try:
            return json.dumps(self.wh.query(sql), default=str)[:4000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {str(exc)[:200]}"

    def profile_daily(self, table: str, date_column: str, days: int = 30) -> str:
        """Return daily row counts for a table over the last N days.

        Args:
            table: Bare table name, for example `fct_visit`.
            date_column: The DATE column to group by, for example `visit_date`.
            days: How many days back to profile. Capped at 120.
        """
        self.tool_calls.append({"tool": "profile_daily", "table": table})
        if self._over_budget():
            return "error: tool call budget exhausted; call submit_diagnosis now"
        t = re.sub(r"[^A-Za-z0-9_]", "", table)
        c = re.sub(r"[^A-Za-z0-9_]", "", date_column)
        n = max(1, min(int(days), 120))
        sql = (
            f"SELECT CAST({c} AS STRING) AS day, COUNT(*) AS rows_on_day "
            "FROM {dataset}." + t + f" WHERE {c} >= DATE_SUB(CURRENT_DATE(), "
            f"INTERVAL {n} DAY) GROUP BY day ORDER BY day"
        )
        try:
            return json.dumps(self.wh.query(sql), default=str)[:6000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}: {str(exc)[:200]}"

    def submit_diagnosis(
        self,
        failure_mode: str,
        confidence: str,
        title: str,
        what_broke: str,
        why_it_is_invisible: str,
        reproducing_query: str,
        likely_causes: List[str],
        suggested_owner: str,
    ) -> str:
        """Record the final diagnosis. Call this exactly once, last.

        Args:
            failure_mode: One of fanout_duplication, silent_stall,
                soft_delete_leak, coverage_gap, freshness_lag,
                referential_break, unknown.
            confidence: high, medium, or low.
            title: One-line issue title naming the table and the problem.
            what_broke: Two or three sentences grounded in the evidence.
            why_it_is_invisible: Why a row-count threshold would miss this.
            reproducing_query: One read-only SELECT that shows the problem.
            likely_causes: One to four ranked upstream causes.
            suggested_owner: Which team or role should look at it.
        """
        try:
            self.result = Diagnosis(
                failure_mode=failure_mode,
                confidence=confidence,
                title=title,
                what_broke=what_broke,
                why_it_is_invisible=why_it_is_invisible,
                reproducing_query=reproducing_query,
                likely_causes=list(likely_causes),
                suggested_owner=suggested_owner,
            )
        except Exception as exc:  # noqa: BLE001 - the model gets to retry
            return f"rejected: {str(exc)[:400]}"
        return "accepted"

    # --- prompt --------------------------------------------------------

    def prompt_for(self, result) -> str:
        return (
            f"Dataset: `{self.qualified}`\n"
            f"Failed contract: {result.check_id} (contract {result.contract})\n"
            f"Table: {result.table}\n"
            f"Severity: {result.severity}\n"
            f"Check summary: {result.summary}\n\n"
            "Evidence from the check layer:\n"
            f"{json.dumps(result.evidence, default=str, indent=1)[:8000]}\n"
        )


def build_agent(session: DiagnosisSession):
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="warehouse_sentinel_diagnosis",
        model=config.MODEL,
        instruction=INSTRUCTION,
        tools=[
            session.run_query,
            session.describe_table,
            session.profile_daily,
            session.submit_diagnosis,
        ],
    )


async def diagnose_async(result, warehouse: Optional[Warehouse] = None):
    """Diagnose one failed CheckResult. Returns (Diagnosis|None, trace)."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    session = DiagnosisSession(warehouse)
    runner = InMemoryRunner(
        agent=build_agent(session), app_name="warehouse-sentinel"
    )
    adk_session = await runner.session_service.create_session(
        app_name="warehouse-sentinel", user_id="sweep"
    )
    message = types.Content(
        role="user", parts=[types.Part(text=session.prompt_for(result))]
    )
    text: List[str] = []
    async for event in runner.run_async(
        user_id="sweep", session_id=adk_session.id, new_message=message
    ):
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                text.append(part.text)
    return session.result, {
        "tool_calls": session.tool_calls,
        "model": config.MODEL,
        "narration": " ".join(text)[-1200:],
    }


def diagnose(result, warehouse: Optional[Warehouse] = None):
    return asyncio.run(
        asyncio.wait_for(
            diagnose_async(result, warehouse), config.AGENT_TIMEOUT_SECONDS
        )
    )


def verify_reproducing_query(warehouse: Warehouse, diagnosis: Diagnosis):
    """Run the model's reproducing query before anything acts on it.

    A diagnosis whose query does not execute is not shippable. Verifying here
    means a broken query is caught by SQL, not by the person who opens the
    ticket.
    """
    sql = (diagnosis.reproducing_query or "").strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        return {"ran": False, "error": "not a SELECT statement", "row_count": 0}
    if FORBIDDEN.search(sql) or ";" in sql:
        return {"ran": False, "error": "refused: write keyword or multiple statements", "row_count": 0}
    try:
        rows = warehouse.query_raw(sql)
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "row_count": 0}
    return {"ran": True, "error": None, "row_count": len(rows), "sample": rows[:3]}
