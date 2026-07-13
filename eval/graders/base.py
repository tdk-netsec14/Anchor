"""Shared grader contract.

A grader turns one (test case, API response) pair into a normalised
:class:`GradeResult`. Every grader returns a score in [0, 1] plus a boolean
pass/fail, so the runner can aggregate across heterogeneous graders without
special-casing any of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GradeResult:
    """One grader's verdict on one test case."""

    grader: str
    #: 0.0-1.0. None means "this grader did not apply to this case".
    score: float | None
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def applicable(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader": self.grader,
            "score": self.score,
            "passed": self.passed,
            "detail": self.detail,
        }
