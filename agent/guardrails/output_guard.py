"""Output guardrails.

Three independent checks run on generated text before it reaches the caller:

1. **Structure** - is the answer well-formed enough to render? A malformed
   generation is retried once with an explicit formatting instruction rather
   than being passed through half-parsed.
2. **PII** - emails, phone numbers and SSN-like values are redacted and the
   response is flagged.
3. **Citation sanity** - a citation that names a source we did not retrieve is
   flagged, because a confident wrong source is worse for a support agent than
   no source at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.observability.logger import get_logger
from agent.observability.metrics import registry

log = get_logger(__name__)

REDACTED = "[REDACTED]"

#: Returned instead of a generation that stayed malformed after one retry.
MALFORMED_ANSWER_MESSAGE = (
    "Anchor could not produce a well-formed answer for that question. "
    "Please rephrase it, or create a support ticket."
)

#: Patterns are ordered most-specific first so an SSN is not partially eaten by
#: the phone-number pattern.
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    (
        "phone",
        re.compile(
            r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?"
            r"(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"
        ),
    ),
    (
        # International dialling, which is not 3-3-4: "+44 20 7946 0958".
        # Requires a leading "+" so ordinary numbers are not swept up. Same
        # "phone" kind as above so the reported flag stays stable.
        "phone",
        re.compile(r"(?<![\w+])\+\d{1,3}[\s.-]?\d[\d\s.-]{5,15}\d(?![\w])"),
    ),
    (
        "iban_like",
        # Long digit runs with spaces/dashes: credit-card or account numbers.
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    ),
)

#: A citation the model produced, e.g. "[S2]" or "expense_reimbursement.pdf".
CITATION_TAG = re.compile(r"\[S(\d+)\]", re.IGNORECASE)
CITATION_FILE = re.compile(r"\b([A-Za-z0-9_.-]+\.pdf)\b")


@dataclass(slots=True)
class OutputCheck:
    """Result of inspecting generated text."""

    text: str
    flags: list[str] = field(default_factory=list)
    #: Sources named by the model that were never retrieved.
    unsupported_sources: list[str] = field(default_factory=list)
    redacted_count: int = 0
    #: False when the model returned something unusable and a retry is worthwhile.
    well_formed: bool = True

    @property
    def blocked(self) -> bool:
        """Answers that must not be returned at all (as opposed to flagged)."""
        return not self.well_formed


def redact_pii(text: str) -> tuple[str, list[str], int]:
    """Replace PII-shaped substrings. Returns (text, kinds, replacements)."""
    kinds: list[str] = []
    count = 0
    result = text

    for kind, pattern in PII_PATTERNS:
        matches = pattern.findall(result)
        if not matches:
            continue
        result = pattern.sub(REDACTED, result)
        kinds.append(kind)
        count += len(matches)

    return result, kinds, count


def check_citations(text: str, allowed: set[str]) -> list[str]:
    """Return citation references in ``text`` that are not in ``allowed``.

    ``allowed`` holds the ``[S<n>]`` tags and document names that were actually
    retrieved. Anything else the model names is a fabrication.
    """
    if not allowed:
        # With no context there is nothing legitimate to cite.
        return sorted(set(CITATION_TAG.findall(text)))

    unsupported: set[str] = set()

    for tag in CITATION_TAG.findall(text):
        if f"S{tag}" not in allowed:
            unsupported.add(f"[S{tag}]")

    for name in CITATION_FILE.findall(text):
        if name not in allowed:
            unsupported.add(name)

    return sorted(unsupported)


def inspect_output(
    text: str,
    *,
    allowed_sources: set[str] | None = None,
    require_citation: bool = False,
) -> OutputCheck:
    """Run every output check over generated text."""
    flags: list[str] = []
    original = text or ""

    if not original.strip():
        return OutputCheck(
            text="",
            flags=["output_empty"],
            well_formed=False,
        )

    redacted, kinds, count = redact_pii(original)
    if kinds:
        flags.append("output_pii_redacted")
        for kind in kinds:
            flags.append(f"output_pii_{kind}")
        registry.record_guardrail_block("output_pii_redacted")
        log.warning(
            "guardrail.pii_redacted",
            context={"kinds": kinds, "count": count},
        )

    if require_citation and allowed_sources and not (
        CITATION_TAG.search(redacted) or CITATION_FILE.search(redacted)
    ):
        flags.append("output_missing_citation")

    unsupported = check_citations(redacted, allowed_sources or set())
    if unsupported:
        flags.append("output_unsupported_source")
        registry.record_guardrail_block("output_unsupported_source")
        log.warning(
            "guardrail.unsupported_source",
            context={"unsupported": unsupported[:5]},
        )

    return OutputCheck(
        text=redacted,
        flags=flags,
        unsupported_sources=unsupported,
        redacted_count=count,
    )


def looks_structured(text: str) -> bool:
    """Cheap well-formedness check for a generated answer.

    Rejects empty output and runaway repetition, which are the two ways a small
    local model most often fails. A retry with a formatting instruction is
    worth it for both.
    """
    stripped = (text or "").strip()
    if len(stripped) < 2:
        return False

    words = stripped.split()
    if len(words) >= 12:
        # Degenerate loops repeat the same token over and over. A legitimate
        # answer never drops below ~15% distinct tokens, whatever its length.
        distinct_ratio = len({w.lower() for w in words}) / len(words)
        if distinct_ratio < 0.15:
            return False
    return True


def build_format_retry_instruction() -> str:
    """The corrective prompt used when a generation comes back malformed."""
    return (
        "Your previous response was empty or malformed. Reply again with a "
        "single plain-text paragraph answering the question. Do not use JSON, "
        "markdown headers, or code fences. If the answer is not in the provided "
        "context, say so plainly and suggest creating a support ticket."
    )
