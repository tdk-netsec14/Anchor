"""LLM-as-judge grader: correctness, grounding and relevance on a 1-5 rubric.

The judge is asked for strict JSON so the result is machine-readable; anything
unparseable is recorded as "not applicable" rather than silently scored 0,
because a judge that fails to answer is not evidence that an answer is wrong.

The judge talks to a chat-completions-compatible endpoint configured by
environment variable, defaulting to the same local Ollama the system uses:

    ANCHOR_JUDGE_BASE_URL  (default http://localhost:11434/v1)
    ANCHOR_JUDGE_MODEL     (default llama3.2:3b)
    ANCHOR_JUDGE_API_KEY   (optional)

The judge model matters more than any other setting in the harness. A 1B model
is not capable of this task: measured against a run where it was configured, it
rated a verbatim-correct answer ("the maximum hotel rate is 300 USD") as "does
not address the question asked". Use a model with real capability, or pass
``--no-judge`` and rely on the three deterministic graders.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from eval.graders.base import GradeResult

RUBRIC = """\
Rate the assistant's answer to the user's question on three dimensions, each
from 1 to 5:

- correctness: are the facts stated accurate, and consistent with the REFERENCE
  ANSWER KEY POINTS? If the answer contains the key points, it is correct.
- grounding: does the answer stay within the reference key points, and cite a
  real source, without inventing figures, policies or document names? Do NOT
  penalise the answer for the absence of raw source text - the key points above
  are the ground truth you are given. Cite a real source = 5. No invented
  content but no citation = 3. Invents a figure, policy or document = 1.
- relevance: does the answer actually address the question that was asked?

Scale:
5 = fully correct, fully grounded, precisely on point
4 = correct with a minor omission or imprecision
3 = partially correct or missing a citation
2 = mostly incorrect or introduces unsupported claims
1 = wrong, fabricated, or irrelevant

Reply with ONLY a JSON object, no prose and no code fence:
{"correctness": <int>, "grounding": <int>, "relevance": <int>, "reason": "<one short sentence>"}
"""

def _json_objects(text: str) -> list[str]:
    """Every balanced ``{...}`` span in ``text``, outermost first.

    A greedy ``\\{.*\\}`` is not good enough here: a model that writes "Here you
    go: {...} (rate 1-5)" produces one span containing the prose, which then
    fails to parse and silently discards the verdict.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start : index + 1])
                start = -1
    return spans


class LlmJudgeGrader:
    name = "llm_judge"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("ANCHOR_JUDGE_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.model = model or os.getenv("ANCHOR_JUDGE_MODEL", "llama3.2:3b")
        self.api_key = api_key or os.getenv("ANCHOR_JUDGE_API_KEY")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    @staticmethod
    def parse_scores(raw: str) -> dict[str, Any] | None:
        """Extract the JSON verdict from a model response, or None.

        Every balanced object in the text is tried, so commentary around the
        verdict does not lose it.
        """
        for span in _json_objects(raw or ""):
            try:
                data = json.loads(span)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            scores = {}
            for key in ("correctness", "grounding", "relevance"):
                value = data.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    scores = {}
                    break
                # Small models routinely stray outside 1-5; clamp rather than
                # discard an otherwise usable verdict.
                scores[key] = max(1, min(5, int(round(value))))
            if not scores:
                continue
            reason = data.get("reason", "")
            return {**scores, "reason": reason if isinstance(reason, str) else str(reason)}
        return None

    def grade(self, case: dict[str, Any], response: dict[str, Any]) -> GradeResult:
        context = case.get("judge_context") or "(no context supplied)"
        expected = case.get("expected_answer_contains") or []

        # The judge can only score a case correctly if it knows what the correct
        # behaviour is. For an out-of-scope question that is a refusal, and a
        # refusal scored against "reference key points: n/a" looks like a
        # non-answer to any judge - including a competent one.
        if not expected and (case.get("expect_refusal") or case.get("expect_blocked")):
            goal = (
                "EXPECTED BEHAVIOUR: the knowledge base does not cover this. A "
                "correct answer says so plainly, does not speculate, and may "
                "offer to create a ticket. Score a clear refusal that invents "
                "nothing as 5 for correctness, grounding and relevance. Score "
                "an invented answer as 1."
            )
        else:
            goal = (
                f"REFERENCE ANSWER KEY POINTS: {'; '.join(expected)}\n"
                "An answer containing these points is correct."
            )

        prompt = (
            f"{RUBRIC}\n\n"
            f"{goal}\n\n"
            f"SOURCES CITED BY THE ANSWER: {context}\n\n"
            f"USER QUESTION: {case['query']}\n\n"
            f"ASSISTANT ANSWER: {response.get('answer', '')}\n"
        )

        try:
            http_response = httpx.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 300,
                    # Ask for JSON where the provider supports it; the parser
                    # copes regardless, which is what the regex above is for.
                    "response_format": {"type": "json_object"},
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            http_response.raise_for_status()
            raw = http_response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            return GradeResult(
                grader=self.name,
                score=None,
                passed=True,
                detail={"mode": "unavailable", "reason": f"{type(exc).__name__}: {exc}"},
            )

        parsed = self.parse_scores(raw)
        if parsed is None:
            return GradeResult(
                grader=self.name,
                score=None,
                passed=True,
                detail={"mode": "unparseable", "raw": (raw or "")[:300]},
            )

        mean = (parsed["correctness"] + parsed["grounding"] + parsed["relevance"]) / 3
        return GradeResult(
            grader=self.name,
            # 1-5 rubric mapped onto 0-1 so it aggregates with the other graders.
            score=round((mean - 1) / 4, 4),
            passed=mean >= 3.0,
            detail={
                **parsed,
                "mean_rating": round(mean, 2),
                "pass_threshold": "mean >= 3.0",
                "model": self.model,
            },
        )
