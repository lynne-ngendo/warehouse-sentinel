"""Structured output for the diagnosis step.

The model must fill this schema. Validation happens at the SDK boundary, so a
malformed response fails loudly instead of reaching the ticket writer.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

FAILURE_MODES = [
    "fanout_duplication",
    "silent_stall",
    "soft_delete_leak",
    "coverage_gap",
    "freshness_lag",
    "referential_break",
    "unknown",
]


class Diagnosis(BaseModel):
    """What the model is allowed to say about a failed contract."""

    failure_mode: Literal[
        "fanout_duplication",
        "silent_stall",
        "soft_delete_leak",
        "coverage_gap",
        "freshness_lag",
        "referential_break",
        "unknown",
    ] = Field(description="Which known failure mode the evidence fits.")

    confidence: Literal["high", "medium", "low"] = Field(
        description="How well the evidence supports that classification."
    )

    title: str = Field(
        max_length=110,
        description="One-line issue title naming the table and the problem.",
    )

    what_broke: str = Field(
        description=(
            "Two or three sentences stating what is wrong, in terms of the "
            "evidence. No speculation beyond what the evidence shows."
        )
    )

    why_it_is_invisible: str = Field(
        description=(
            "One or two sentences on why a threshold alert on row counts would "
            "not catch this. Say so plainly if a threshold would catch it."
        )
    )

    reproducing_query: str = Field(
        description=(
            "A single read-only BigQuery SQL statement that shows the problem. "
            "Use the fully qualified table names given in the evidence. No DDL, "
            "no DML."
        )
    )

    likely_causes: List[str] = Field(
        min_length=1,
        max_length=4,
        description="Ranked upstream causes worth checking first.",
    )

    suggested_owner: str = Field(
        description="Which team or role should look at this, in a few words."
    )
