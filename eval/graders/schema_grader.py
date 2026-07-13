"""Schema grader: does the API response satisfy the QueryResponse contract?

Cheap and deterministic, and it runs first - if the shape is wrong, the
content-based graders have nothing meaningful to score, so they should not be
trusted on that response.
"""

from __future__ import annotations

from typing import Any

from eval.graders.base import GradeResult

REQUIRED_FIELDS = (
    "answer",
    "sources",
    "model_used",
    "tool_calls",
    "latency_ms",
    "tokens_used",
    "estimated_cost_usd",
    "guardrail_flags",
    "request_id",
)


def grade(case: dict[str, Any], response: dict[str, Any]) -> GradeResult:
    missing = [f for f in REQUIRED_FIELDS if f not in response]
    if missing:
        return GradeResult(
            grader="schema",
            score=0.0,
            passed=False,
            detail={"missing_fields": missing},
        )

    type_errors: list[str] = []

    def expect(name: str, types: tuple[type, ...]) -> None:
        if not isinstance(response[name], types):
            type_errors.append(
                f"{name} should be {'/'.join(t.__name__ for t in types)}, "
                f"got {type(response[name]).__name__}"
            )

    expect("answer", (str,))
    expect("sources", (list,))
    expect("model_used", (str,))
    expect("tool_calls", (list,))
    expect("latency_ms", (int, float))
    expect("tokens_used", (int,))
    expect("estimated_cost_usd", (int, float))
    expect("guardrail_flags", (list,))
    expect("request_id", (str,))

    if isinstance(response["answer"], str) and not response["answer"].strip():
        type_errors.append("answer is empty")

    if isinstance(response["tool_calls"], list):
        for index, call in enumerate(response["tool_calls"]):
            if not isinstance(call, dict) or "name" not in call:
                type_errors.append(f"tool_calls[{index}] is not a tool call record")

    passed = not type_errors
    return GradeResult(
        grader="schema",
        score=1.0 if passed else 0.0,
        passed=passed,
        detail={"type_errors": type_errors, "request_id": response.get("request_id")},
    )
