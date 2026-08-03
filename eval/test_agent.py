"""Evaluation suite as pytest regressions.

`run_eval.py` produces a report for humans; this file turns the same fixed test
cases into assertions so an answer-quality regression fails CI.

Two layers:

* **Always runs** - grader unit tests, with hand-written responses. These need
  no server, no model and no network, and they are what actually guards against
  a grader silently passing everything.
* **Requires a running API** - the live cases from ``test_cases.json``. Skipped
  with a clear reason when the Anchor API is not reachable, so the suite is
  usable on a laptop with nothing else running.

    pytest eval/                                  # graders only
    ANCHOR_BASE_URL=http://localhost:8000 pytest eval/   # everything
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.graders import exact_match_grader, schema_grader  # noqa: E402
from eval.graders.llm_judge_grader import LlmJudgeGrader  # noqa: E402
from eval.graders.semantic_similarity_grader import (  # noqa: E402
    SemanticGrader,
    split_sentences,
)

BASE_URL = os.getenv("ANCHOR_BASE_URL", "http://localhost:8000")
CASES_PATH = ROOT / "eval" / "test_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _api_is_up() -> bool:
    try:
        import httpx

        response = httpx.get(f"{BASE_URL.rstrip('/')}/health", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


requires_api = pytest.mark.skipif(
    not _api_is_up(),
    reason=f"Anchor API is not reachable at {BASE_URL} (start it with `docker compose up`)",
)


# ==========================================================================
# Layer 1: grader unit tests (always run)
# ==========================================================================
def _valid_response(**overrides: Any) -> dict[str, Any]:
    body = {
        "answer": "You get 25 days of paid annual leave per year [S1].",
        "sources": ["hr_leave_policy.pdf, p.1 [hr_leave_policy.pdf#p1#c0]"],
        "model_used": "ollama/llama3.2:1b",
        "tool_calls": [],
        "latency_ms": 123.4,
        "tokens_used": 250,
        "estimated_cost_usd": 0.0,
        "guardrail_flags": [],
        "request_id": "11111111-2222-3333-4444-555555555555",
    }
    body.update(overrides)
    return body


class TestSchemaGrader:
    def test_valid_response_passes(self) -> None:
        result = schema_grader.grade({"id": "x"}, _valid_response())
        assert result.passed
        assert result.score == 1.0

    def test_missing_field_fails(self) -> None:
        body = _valid_response()
        del body["tokens_used"]
        result = schema_grader.grade({"id": "x"}, body)
        assert not result.passed
        assert "tokens_used" in result.detail["missing_fields"]

    def test_wrong_type_fails(self) -> None:
        result = schema_grader.grade({"id": "x"}, _valid_response(latency_ms="fast"))
        assert not result.passed
        assert any("latency_ms" in e for e in result.detail["type_errors"])

    def test_empty_answer_fails(self) -> None:
        assert not schema_grader.grade({"id": "x"}, _valid_response(answer="  ")).passed


class TestExactMatchGrader:
    def test_all_expected_fragments_present_passes(self) -> None:
        case = {"expected_answer_contains": ["25 days", "2.08"]}
        body = _valid_response(answer="25 days, accruing 2.08 days per month [S1].")
        assert exact_match_grader.grade(case, body).passed

    def test_missing_fragment_fails_and_names_it(self) -> None:
        case = {"expected_answer_contains": ["25 days", "2.08"]}
        body = _valid_response(answer="You get 25 days of annual leave [S1].")
        result = exact_match_grader.grade(case, body)
        assert not result.passed
        assert result.detail["missing"] == ["2.08"]
        assert result.score == 0.5

    def test_refusal_is_recognised(self) -> None:
        case = {"expected_answer_contains": [], "expect_refusal": True}
        body = _valid_response(answer="I do not have information on that in the knowledge base.")
        assert exact_match_grader.grade(case, body).passed

    @pytest.mark.parametrize(
        "answer",
        [
            # The phrasings Anchor's own model actually produces.
            "The context provided does not contain any information on the "
            "airspeed velocity of an unladen swallow.",
            "The knowledge base has no information on this question.",
            "The knowledge base does not cover this topic. I recommend "
            "creating a ticket to request more information.",
            "I am not able to find anything on this in the knowledge base.",
        ],
    )
    def test_real_refusal_phrasings_are_recognised(self, answer: str) -> None:
        case = {"expected_answer_contains": [], "expect_refusal": True}
        assert exact_match_grader.grade(case, {"answer": answer}).passed, answer

    def test_offering_a_ticket_alone_is_not_a_refusal(self) -> None:
        """A ticket offer can accompany a real answer; do not treat it as one."""
        case = {"expected_answer_contains": [], "expect_refusal": True}
        assert not exact_match_grader.grade(
            case, {"answer": "I have created a ticket to raise this with finance."}
        ).passed

    def test_hallucination_on_a_refusal_case_fails(self) -> None:
        case = {"expected_answer_contains": [], "expect_refusal": True}
        body = _valid_response(answer="The unladen swallow flies at about 11 metres per second.")
        assert not exact_match_grader.grade(case, body).passed

    def test_no_expectation_is_not_applicable(self) -> None:
        result = exact_match_grader.grade({"expected_answer_contains": []}, _valid_response())
        assert result.score is None
        assert not result.applicable


class TestSemanticGrader:
    def test_sentence_splitter_survives_decimals_and_abbreviations(self) -> None:
        assert split_sentences("The rate is 25.0 days. That is p.1 of the policy.") == [
            "The rate is 25.0 days.",
            "That is p.1 of the policy.",
        ]

    def test_paraphrase_scores_above_an_unrelated_sentence(self) -> None:
        grader = SemanticGrader()
        related = _valid_response(
            answer="Employees receive twenty-five days of paid annual leave each year [S1]."
        )
        unrelated = _valid_response(answer="The office wifi password is posted on the intranet.")
        case = {"expected_answer_contains": ["25 days of paid annual leave"]}

        if grader._load() is None:
            pytest.skip(f"embedding model unavailable: {grader._unavailable_reason}")

        assert grader.grade(case, related).score > grader.grade(case, unrelated).score

    def test_not_applicable_without_expectations(self) -> None:
        grader = SemanticGrader()
        assert grader.grade({"expected_answer_contains": []}, _valid_response()).score is None


class TestLlmJudge:
    def test_parses_well_formed_json(self) -> None:
        parsed = LlmJudgeGrader.parse_scores(
            '{"correctness": 5, "grounding": 4, "relevance": 5, "reason": "good"}'
        )
        assert parsed["correctness"] == 5
        assert parsed["reason"] == "good"

    def test_parses_json_wrapped_in_prose_or_fences(self) -> None:
        parsed = LlmJudgeGrader.parse_scores(
            'Sure!\n```json\n{"correctness": 4, "grounding": 3, "relevance": 4}\n```'
        )
        assert parsed["grounding"] == 3

    def test_clamps_out_of_range_scores(self) -> None:
        parsed = LlmJudgeGrader.parse_scores('{"correctness": 9, "grounding": 0, "relevance": 3}')
        assert parsed["correctness"] == 5
        assert parsed["grounding"] == 1

    def test_rejects_unparseable_output(self) -> None:
        assert LlmJudgeGrader.parse_scores("I think it's pretty good actually") is None
        assert LlmJudgeGrader.parse_scores("") is None

    def test_rejects_missing_dimensions(self) -> None:
        assert LlmJudgeGrader.parse_scores('{"correctness": 5, "grounding": 4}') is None


# ==========================================================================
# Layer 2: live evaluation (requires a running Anchor API)
# ==========================================================================
def test_test_case_file_is_well_formed() -> None:
    """The suite is only meaningful if the cases themselves are sound."""
    cases = _load_cases()
    assert 15 <= len(cases) <= 20, f"expected 15-20 cases, found {len(cases)}"

    categories = {c["category"] for c in cases}
    assert {"factual", "tool_use", "adversarial", "out_of_scope"} <= categories

    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "test case ids must be unique"

    for case in cases:
        assert case["id"] and case["query"] and case["category"]
        assert "expected_answer_contains" in case
        assert "expected_tool_call" in case
        assert case["category"] in {"factual", "tool_use", "adversarial", "out_of_scope"}


#: Credentials the live cases authenticate with. `/query` is tenant-scoped, so
#: a demo token from `/auth/token` — which names no workspace — is refused by
#: the very route these cases exercise. The harness therefore signs in as a
#: real account, creating it on the first run.
EVAL_EMAIL = "eval-pytest@example.com"
EVAL_PASSWORD = "evaluation-harness-passphrase"


def _live_token(client) -> str:  # noqa: ANN001 - an httpx.Client
    """Register (first run) or log in, and return an access token.

    Falls back to the development token endpoint for a deployment with no
    database, where there are no accounts to register.
    """
    root = BASE_URL.rstrip("/")
    registered = client.post(
        f"{root}/auth/register",
        json={
            "email": EVAL_EMAIL,
            "password": EVAL_PASSWORD,
            "full_name": "Evaluation Harness",
            "workspace_name": "Evaluation",
        },
        timeout=30.0,
    )
    if registered.status_code == 201:
        return registered.json()["access_token"]

    logged_in = client.post(
        f"{root}/auth/login",
        json={"email": EVAL_EMAIL, "password": EVAL_PASSWORD},
        timeout=30.0,
    )
    if logged_in.status_code == 200:
        return logged_in.json()["access_token"]

    return client.post(
        f"{root}/auth/token",
        json={"username": "eval-pytest", "role": "user"},
        timeout=30.0,
    ).json()["access_token"]


@requires_api
@pytest.mark.integration
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_case_against_live_api(case: dict[str, Any]) -> None:
    """One live request per test case, asserted the way CI needs it."""
    import httpx

    with httpx.Client() as client:
        token = _live_token(client)

        response = client.post(
            f"{BASE_URL.rstrip('/')}/query",
            json={"query": case["query"], "session_id": case["id"]},
            headers={"Authorization": f"Bearer {token}"},
            timeout=300.0,
        )

    if case["category"] == "adversarial" or case.get("expect_blocked"):
        assert response.status_code == 400, (
            f"{case['id']} should have been rejected by the input guard, "
            f"got HTTP {response.status_code}: {response.text[:200]}"
        )
        assert response.json().get("guardrail_flags")
        return

    assert response.status_code == 200, f"{case['id']} -> HTTP {response.status_code}: {response.text[:200]}"
    body = response.json()

    assert schema_grader.grade(case, body).passed, "response violates the QueryResponse schema"

    expected_tool = case.get("expected_tool_call")
    if expected_tool:
        called = [c["name"] for c in body["tool_calls"]]
        assert expected_tool in called, f"{case['id']} expected tool '{expected_tool}', called {called}"

    for fragment in case.get("expected_answer_contains") or []:
        assert exact_match_grader.normalise(fragment) in exact_match_grader.normalise(
            body["answer"]
        ), f"{case['id']} missing expected content: {fragment!r}"


@requires_api
@pytest.mark.integration
def test_full_evaluation_run_meets_the_quality_gate() -> None:
    """The runner must work, and the suite must clear its quality bar.

    The gate is a pass *rate*, not zero failures. A 3B local model will
    occasionally miss a case, and a suite that only ever passes at 100% is one
    that gets switched off. Set ``ANCHOR_EVAL_MIN_PASS_RATE`` to tighten or
    loosen it; the default reflects what a small local model reliably achieves
    and still fails loudly on a real regression.
    """
    import json as _json

    from eval.run_eval import RESULTS_DIR, main

    before = {p.name for p in RESULTS_DIR.glob("*.json")} if RESULTS_DIR.exists() else set()

    main(["--base-url", BASE_URL, "--no-judge"])

    new_files = [p for p in RESULTS_DIR.glob("*.json") if p.name not in before]
    assert new_files, "the evaluation run produced no results file"

    report = _json.loads(new_files[-1].read_text(encoding="utf-8"))
    summary = report["summary"]

    minimum = float(os.getenv("ANCHOR_EVAL_MIN_PASS_RATE", "0.5"))
    assert summary["error_cases"] == 0, (
        f"{summary['error_cases']} transport errors during the run"
    )
    assert summary["pass_rate"] >= minimum, (
        f"pass rate {summary['pass_rate']:.2f} is below the {minimum:.2f} gate; "
        f"failures: {[r['id'] for r in report['results'] if not r.get('passed')]}"
    )


class TestJudgeParsingRobustness:
    def test_verdict_wrapped_in_prose_is_not_lost(self) -> None:
        """A greedy regex used to swallow commentary and discard the verdict."""
        raw = (
            'Here is my assessment of the answer.\n'
            '{"correctness": 5, "grounding": 4, "relevance": 5, "reason": "good"}\n'
            "Let me know if you need more."
        )
        parsed = LlmJudgeGrader.parse_scores(raw)
        assert parsed is not None
        assert parsed["correctness"] == 5
        assert parsed["grounding"] == 4

    def test_nested_objects_do_not_break_parsing(self) -> None:
        raw = '{"correctness": 4, "grounding": 3, "relevance": 4, "detail": {"a": 1}}'
        assert LlmJudgeGrader.parse_scores(raw)["correctness"] == 4

    def test_braces_inside_strings_do_not_confuse_the_scanner(self) -> None:
        raw = '{"correctness": 5, "grounding": 5, "relevance": 5, "reason": "used } and {"}'
        parsed = LlmJudgeGrader.parse_scores(raw)
        assert parsed["correctness"] == 5
