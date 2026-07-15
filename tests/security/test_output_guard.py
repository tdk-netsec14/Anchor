"""Adversarial output tests: PII redaction, fabricated citations, malformed output."""

from __future__ import annotations

import pytest

from agent.guardrails.output_guard import (
    REDACTED,
    check_citations,
    inspect_output,
    looks_structured,
    redact_pii,
)

pytestmark = pytest.mark.security

ALLOWED = {"S1", "S2", "hr_leave_policy.pdf", "it_support_policy.pdf"}


# --------------------------------------------------------------------------
# PII
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Contact alice.smith@corp.example for access", "email"),
        ("My number is 555-123-4567 if that helps", "phone"),
        ("SSN 123-45-6789 on file", "ssn"),
        ("Call +44 20 7946 0958 tomorrow", "phone"),
        ("Card 4111 1111 1111 1111 was used", "iban_like"),
    ],
)
def test_pii_patterns_are_redacted(text: str, kind: str) -> None:
    redacted, kinds, count = redact_pii(text)
    assert REDACTED in redacted
    assert kind in kinds
    assert count >= 1
    # The original value must be gone.
    assert "alice.smith@corp.example" not in redacted
    assert "555-123-4567" not in redacted
    assert "123-45-6789" not in redacted


def test_pii_redaction_sets_the_required_flag() -> None:
    result = inspect_output("Reach me at bob@example.com", allowed_sources=ALLOWED)
    assert "output_pii_redacted" in result.flags
    assert "output_pii_email" in result.flags
    assert "bob@example.com" not in result.text


def test_clean_text_is_not_flagged() -> None:
    result = inspect_output(
        "You get 25 days of paid annual leave [S1].", allowed_sources=ALLOWED
    )
    assert "output_pii_redacted" not in result.flags
    assert result.flags == []


# --------------------------------------------------------------------------
# Fabricated citations
# --------------------------------------------------------------------------
def test_citation_to_a_retrieved_source_is_accepted() -> None:
    result = inspect_output("The limit is 25 days [S1].", allowed_sources=ALLOWED)
    assert "output_unsupported_source" not in result.flags


def test_citation_to_an_unretrieved_source_is_flagged() -> None:
    result = inspect_output(
        "The limit is 25 days [S7].", allowed_sources=ALLOWED
    )
    assert "output_unsupported_source" in result.flags
    assert "[S7]" in result.unsupported_sources


def test_fabricated_document_name_is_flagged() -> None:
    result = inspect_output(
        "According to secret_handover_notes.pdf the answer is yes.",
        allowed_sources=ALLOWED,
    )
    assert "output_unsupported_source" in result.flags
    assert "secret_handover_notes.pdf" in result.unsupported_sources


def test_citations_with_no_context_at_all_are_suspect() -> None:
    result = inspect_output("The answer is 42 [S1].", allowed_sources=set())
    assert "output_unsupported_source" in result.flags


def test_check_citations_handles_real_documents() -> None:
    assert check_citations("see hr_leave_policy.pdf", ALLOWED) == []
    assert check_citations("see other.pdf", ALLOWED) == ["other.pdf"]


# --------------------------------------------------------------------------
# Malformed output
# --------------------------------------------------------------------------
def test_empty_output_is_not_well_formed() -> None:
    result = inspect_output("", allowed_sources=ALLOWED)
    assert result.well_formed is False
    assert "output_empty" in result.flags
    assert result.blocked is True


@pytest.mark.parametrize("text", ["", "   ", "\n", "x"])
def test_degenerate_output_is_detected(text: str) -> None:
    assert looks_structured(text) is False


def test_runaway_repetition_is_detected() -> None:
    assert looks_structured("the the the the the the the the the the the the") is False


def test_a_normal_answer_is_structured() -> None:
    assert looks_structured("You get 25 days of annual leave per year [S1].") is True


def test_missing_citation_is_flagged_when_context_was_available() -> None:
    result = inspect_output(
        "You get 25 days of annual leave.", allowed_sources=ALLOWED, require_citation=True
    )
    assert "output_missing_citation" in result.flags


def test_pii_and_fabrication_are_reported_independently() -> None:
    result = inspect_output(
        "Email bob@example.com, see [S9] in fake_doc.pdf", allowed_sources=ALLOWED
    )
    assert "output_pii_redacted" in result.flags
    assert "output_unsupported_source" in result.flags
