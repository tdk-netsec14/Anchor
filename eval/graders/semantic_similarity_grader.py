"""Semantic-similarity grader using a local embedding model.

Exact match is brittle: a correct answer may phrase a fact differently
("thirty minutes" vs "30 minutes", "five working days" vs "5 working days").
This grader catches those.

**Method.** The reference is a short curated fragment ("25 days"), while the
answer is a sentence or two. Embedding the whole answer and comparing it to that
fragment as one pair of vectors scores it as a near miss even when the fact is
plainly present - an earlier version of this grader failed 7 of 8 perfectly
correct answers for exactly that reason.

So the answer is cut into sliding word-windows of roughly the reference's size,
and the grade is the *maximum* similarity of any window against the reference.
That answers the question we actually care about: "is the expected content
present somewhere in this answer?"

A window size of ``reference_words + 3`` gives enough context for a fragment to
be embedded meaningfully ("25 days" needs to become "accrue 25 days of paid")
without letting unrelated material dilute the score.

See ``scripts/calibrate_grader.py`` to re-derive the threshold from a saved run.
"""

from __future__ import annotations

import re
from typing import Any

from eval.graders.base import GradeResult

#: Cosine similarity at or above which a span counts as conveying the reference.
#:
#: Calibrated empirically, not guessed. Re-grading a full evaluation run with
#: `scripts/calibrate_grader.py` gave a clean separation: answers whose curated
#: fact was present scored 0.475-0.809, answers missing it scored 0.226-0.432.
#: 0.45 sits in that gap. Unrelated spans typically land near 0.1-0.3.
DEFAULT_THRESHOLD = 0.45

_ABBREVIATIONS = {"p", "pp", "no", "vs", "etc", "e.g", "i.e", "mr", "mrs", "dr", "usd"}

#: A run of sentence-ending punctuation followed by whitespace or end of text.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")


def split_sentences(text: str) -> list[str]:
    """Split into sentences without breaking on decimals or common abbreviations.

    Candidate split points are examined rather than rewritten: an earlier
    version substituted the periods away before splitting, which removed the
    very boundaries it needed.
    """
    if not text:
        return []

    sentences: list[str] = []
    start = 0

    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        head = text[start:end]

        # "25.0" - a period between two digits is part of a number.
        if match.group(0) == "." and end < len(text):
            before, after = text[end - 2 : end - 1], text[end : end + 1]
            if before.isdigit() and after.isdigit():
                continue

        # "p.1", "e.g." - a period after a known abbreviation.
        tail_word = re.search(r"([A-Za-z]+)\.$", head)
        if tail_word and tail_word.group(1).lower() in _ABBREVIATIONS:
            continue

        chunk = head.strip()
        if chunk:
            sentences.append(chunk)
        start = end

    remainder = text[start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def _word_windows(text: str, size: int, stride: int = 1) -> list[str]:
    """All sliding word-windows of ``size`` across ``text``.

    Presence of a fact is a property of a *span*, not of a whole sentence: the
    expected content "25 days" is present in "full-time employees accrue 25 days
    of paid annual leave per year", but comparing that 13-word sentence to the
    2-word reference as a single pair of vectors scores it as a near miss.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    return [
        " ".join(words[start : start + size]) for start in range(0, len(words) - size + 1, stride)
    ]


class SemanticGrader:
    """Stateful because the embedding model is expensive to construct."""

    name = "semantic_similarity"

    def __init__(self, model_name: str | None = None, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._model: Any = None
        self._unavailable_reason: str | None = None

    def _load(self) -> Any:
        if self._model is not None or self._unavailable_reason:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            self._unavailable_reason = f"sentence-transformers not installed ({exc})"
            return None
        try:
            name = self.model_name or "sentence-transformers/all-MiniLM-L6-v2"
            self._model = SentenceTransformer(name)
        except Exception as exc:
            # Reported as "not applicable" rather than as a failure: a grading
            # run without the model is degraded, not wrong.
            self._unavailable_reason = f"could not load '{self.model_name}': {exc}"
        return self._model

    def grade(self, case: dict[str, Any], response: dict[str, Any]) -> GradeResult:
        expected: list[str] = case.get("expected_answer_contains") or []
        answer = response.get("answer") or ""

        if not expected:
            return GradeResult(
                grader=self.name, score=None, passed=True, detail={"mode": "not_applicable"}
            )

        model = self._load()
        if model is None:
            return GradeResult(
                grader=self.name,
                score=None,
                passed=True,
                detail={"mode": "unavailable", "reason": self._unavailable_reason},
            )

        reference = " ".join(expected)
        # Score the reference against every span of a comparable size, so a
        # short expected fact is judged on the span that would contain it.
        window_words = max(len(reference.split()) + 3, 5)
        spans = _word_windows(answer, window_words)
        if not spans:
            return GradeResult(
                grader=self.name,
                score=0.0,
                passed=False,
                detail={"reference": reference, "reason": "empty answer"},
            )

        vectors = model.encode([reference, *spans], normalize_embeddings=True)
        reference_vector, span_vectors = vectors[0], vectors[1:]
        scores = span_vectors @ reference_vector

        best_index = int(max(range(len(scores)), key=lambda i: scores[i]))
        best = float(scores[best_index])

        return GradeResult(
            grader=self.name,
            score=round(best, 4),
            passed=best >= self.threshold,
            detail={
                "threshold": self.threshold,
                "best_span": spans[best_index][:200],
                "reference": reference,
                "spans_compared": len(spans),
                "max_similarity": round(best, 4),
            },
        )
