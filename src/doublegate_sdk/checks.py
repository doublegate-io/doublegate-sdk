# Modified for standalone doublegate_sdk namespace; see NOTICE.
"""Transport-independent deterministic inspection results; no authority handles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Finding:
    """One finding container; callers must sanitize fields before construction.

    This dataclass does not mask or validate supplied strings. The bundled
    evaluator uses fixed diagnostics and empty excerpts, never input content.
    """

    kind: str
    severity: str
    detail: str
    start: int = 0
    end: int = 0
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity, "detail": self.detail,
                "start": self.start, "end": self.end, "excerpt": self.excerpt}


@dataclass(frozen=True, slots=True)
class ScanResult:
    outcome: str                                    # "clean" | "flagged"
    findings: tuple[Finding, ...] = ()
    masked: str = ""                                # the body a gate prompt may see

    @property
    def flagged(self) -> bool:
        return self.outcome == "flagged"

    def to_payload(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "findings": [f.to_dict() for f in self.findings]}
