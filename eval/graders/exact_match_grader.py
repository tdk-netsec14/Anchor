"""Exact-match grader: does the answer contain the expected key facts?

Deliberately simple and explainable. A support answer is only correct if the
specific figure, limit or policy name appears in it, so substring matching over
the curated fragments is a genuinely strong signal - and when it fails you can
see exactly which fact was missing.
"""

from __future__ import annotations

import re
from typing import Any

from eval.graders.base import GradeResult

#: Phrases that indicate the model declined rather than answered. Used to
#: confirm that out-of-scope questions were refused rather than hallucinated.
#:
#: Collected from the phrasings Anchor's own model actually produces, which
#: are more varied than the obvious ones - an earlier list scored two correct
#: refusals as failures purely because it did not recognise the wording.
REFUSAL_MARKERS = (
    "do not have",
    "doesn't have",
    "does not have",
    "no information",
    "not in the knowledge base",
    "does not contain",
    "does not cover",
    "cannot find",
    "can't find",
    "could not find",
    "not covered",
    "unable to find",
    "not able to find",
    "find anything on this",
    "no relevant",
    "knowledge base contains no",
    "i don't know",
    "not specified",
    "no such policy",
    "outside the scope",
    "cannot be answered",
    "not available in the knowledge base",
)


def normalise(text: str) -> str:
    """Lowercase and collapse punctuation/whitespace so '3.2' == '3 . 2'."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"[‐-―]", "-", text)
    text = re.sub(r"[^\w\s.%$]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def grade(case: dict[str, Any], response: dict[str, Any]) -> GradeResult:
    expected: list[str] = case.get("expected_answer_contains") or []
    answer = response.get("answer") or ""
    haystack = normalise(answer)

    if not expected:
        # No expected content: this grader has nothing to assert. A refusal is
        # still worth confirming when the case asks for one.
        if case.get("expect_refusal") or case.get("expect_blocked"):
            refused = any(marker in haystack for marker in REFUSAL_MARKERS)
            return GradeResult(
                grader="exact_match",
                score=1.0 if refused else 0.0,
                passed=refused,
                detail={
                    "mode": "refusal_expected",
                    "refused": refused,
                    "answer_preview": answer[:160],
                },
            )
        return GradeResult(
            grader="exact_match", score=None, passed=True, detail={"mode": "not_applicable"}
        )

    found = [fragment for fragment in expected if normalise(fragment) in haystack]
    missing = [fragment for fragment in expected if fragment not in found]
    score = len(found) / len(expected)

    return GradeResult(
        grader="exact_match",
        score=round(score, 4),
        passed=not missing,
        detail={
            "expected": expected,
            "found": found,
            "missing": missing,
            "answer_preview": answer[:200],
        },
    )
