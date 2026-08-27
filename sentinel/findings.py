"""Result types for the deterministic check layer."""

import dataclasses
import json
from typing import Any, Dict, Optional


@dataclasses.dataclass
class CheckResult:
    """The outcome of one contract check.

    `passed` is decided by SQL alone. No model is consulted, so a finding is a
    fact about the data rather than an opinion about it.
    """

    check_id: str
    contract: int
    table: str
    severity: str
    title: str
    passed: bool
    evidence: Dict[str, Any] = dataclasses.field(default_factory=dict)
    summary: str = ""
    error: Optional[str] = None
    duration_ms: int = 0
    bytes_billed: int = 0

    @property
    def status(self):
        if self.error:
            return "error"
        return "pass" if self.passed else "fail"

    def to_dict(self):
        d = dataclasses.asdict(self)
        d["status"] = self.status
        return d

    def to_json(self):
        return json.dumps(self.to_dict(), default=str, indent=2)
