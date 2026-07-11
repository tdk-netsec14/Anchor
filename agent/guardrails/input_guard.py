"""Input guardrails.

Runs before retrieval and generation. Two jobs:

* reject requests that are malformed or oversized (cheap, unconditional);
* detect *likely* prompt-injection and system-prompt-extraction attempts.

SECURITY NOTE - read this before trusting the injection filter. This is
deliberately lightweight pattern matching: it raises the cost of a naive attack
and catches the common phrasings, and it is **not** a security boundary. It is
trivially bypassed by paraphrase, by non-English text, by encoding tricks, or by
splitting an attack across several turns. The actual defences are the ones that
do not depend on spotting an attack: the model never receives the system prompt
in a form it can be asked to print, tools are allow-listed and argument-checked,
and the user query is always placed in its own message rather than concatenated
into the instructions. Treat the flags below as a telemetry signal, not a
guarantee, and see the README for the production upgrade path.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.observability.metrics import registry

log = get_logger(__name__)

MIN_QUERY_CHARS = 2

#: Phrasings that try to override the system prompt or extract it.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b[^.!?]{0,40}?"
            r"\b(previous|prior|above|earlier|all|any|your)\b[^.!?]{0,20}?"
            r"\b(instruction|prompt|rule|direction|guideline|command)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "reveal_system_prompt",
        re.compile(
            r"\b(reveal|show|print|repeat|output|display|disclose|dump|expose|leak|"
            r"tell me|what (is|are|was))\b[^.!?]{0,40}?"
            # Plurals matter: `\b` after a bare "instruction" will not match
            # "instructions", so each alternative carries its own `s?`.
            r"\b(system prompts?|system messages?|initial instructions?|"
            r"hidden instructions?|your instructions?|your prompts?|"
            r"original instructions?|prompt templates?|developer message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\b(you are now|act as|pretend to be|roleplay as|simulate being|"
            r"from now on you are|new instructions?:)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "guardrail_evasion",
        re.compile(
            r"\b(developer mode|jailbreak|dan mode|do anything now|"
            r"without (any )?(restrictions|limits|filters)|bypass (your |the )?"
            r"(guardrails?|filters?|safety))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "context_manipulation",
        re.compile(
            r"\b(repeat (everything|all) (above|before)|print (everything|all) "
            r"(above|before)|what did i just say|verbatim)\b",
            re.IGNORECASE,
        ),
    ),
)

#: Characters that carry no meaning in a support question but defeat naive
#: substring matching when used to smuggle an instruction past the filter.
_ZERO_WIDTH = re.compile(r"[​-‏ - ⁠﻿]")


@dataclass(slots=True)
class InputCheck:
    """Outcome of inspecting an incoming query."""

    allowed: bool
    reason: str = ""
    flags: list[str] = field(default_factory=list)
    normalized: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed


def normalize(text: str) -> str:
    """Fold away tricks used to smuggle keywords past a substring filter."""
    # NFKC collapses fullwidth/compatibility forms ("ｉｇｎｏｒｅ" -> "ignore").
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def check_query(raw: str) -> InputCheck:
    """Validate and screen an incoming user query."""
    settings = get_settings()

    if raw is None or not isinstance(raw, str):
        return _block("query_not_a_string", ["input_invalid_type"])

    if len(raw) > settings.QUERY_MAX_CHARS:
        return _block(
            f"Query is too long ({len(raw)} characters; limit is "
            f"{settings.QUERY_MAX_CHARS}).",
            ["input_oversized"],
        )

    normalized = normalize(raw)

    if len(normalized) < MIN_QUERY_CHARS:
        return _block(
            "Query is empty or too short to answer.", ["input_empty"]
        )

    # Only normalise for matching; the original text is what gets embedded and
    # shown to the model, so the user sees their own words.
    haystack = normalized.lower()
    flags = [name for name, pattern in INJECTION_PATTERNS if pattern.search(haystack)]

    if flags:
        # Blocked outright: these are unambiguous extraction/override attempts,
        # not questions that happen to contain the word "instructions".
        return _block(
            "This request looks like a prompt-injection or system-prompt "
            "extraction attempt and was not processed. Anchor answers questions "
            "about internal policies and documentation.",
            [f"input_{flag}" for flag in flags],
        )

    return InputCheck(allowed=True, normalized=normalized, flags=[])


def _block(reason: str, flags: list[str]) -> InputCheck:
    for flag in flags:
        registry.record_guardrail_block(flag)
    log.warning("guardrail.input_blocked", context={"flags": flags, "reason": reason})
    return InputCheck(allowed=False, reason=reason, flags=flags)
