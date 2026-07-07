"""Recovery of tool calls that a model emitted as plain text.

Small local models (anything in the 1B-3B range, which is what most people
actually run through Ollama) frequently imitate the tool schema by *writing* the
JSON into their message content instead of using the provider's tool-calling
channel. The request then "succeeds", returns no tool calls, and the raw blob
is handed to the user as the answer.

This module recognises that shape and converts it back into a real
:class:`~agent.routing.providers.base.ToolCall`, so the rest of the loop - and
in particular the registry's argument validation - handles it identically to a
native tool call.

The matching is deliberately strict, because a false positive would execute a
tool the model did not mean to call:

* the content must be *entirely* a JSON object, not contain one;
* that object must carry a recognisable function-call shape;
* the name must already be a registered tool (the caller supplies the set);
* arguments still go through the registry's schema validation as normal.

Anything that does not match all of that is left alone and returned to the user
as an ordinary answer.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.routing.providers.base import ToolCall

#: A single balanced JSON object, possibly wrapped in a ```json fence.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

#: Keys that identify an OpenAI-style function call.
_NAME_KEYS = ("name",)
_ARG_KEYS = ("arguments", "parameters", "args", "input")


def _extract_object(content: str) -> tuple[dict[str, Any] | None, str]:
    """Parse the content as a JSON object. Returns (object, preceding text).

    Two shapes are accepted, both of which small models actually emit:

    * the content is exactly one JSON object;
    * the content is prose *followed by* a JSON object, e.g.
      ``"I'll use the search_kb tool:\\n{...}"``.

    The preceding text is returned too, because one common shape puts the tool
    name on the previous line and only the arguments in the JSON.
    """
    text = (content or "").strip()
    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()

    for candidate, prefix in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed, prefix
    return None, ""


def _json_candidates(text: str) -> list[tuple[str, str]]:
    """Candidate ``(json, text_before_it)`` pairs, most specific first."""
    candidates: list[tuple[str, str]] = []
    if text.startswith("{") and text.endswith("}"):
        candidates.append((text, ""))

    # Trailing object: find the '{' that opens the final '}' so nested objects
    # are captured, and keep the text before it as a possible name hint.
    if text.endswith("}"):
        start = text.find("{", 0, len(text) - 1)
        if start > 0:
            candidates.append((text[start:], text[:start]))
    return candidates


def _unpack(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Find the function name and arguments in any of the shapes seen in the wild.

    Handles ``{"name": ..., "arguments": {...}}``,
    ``{"type": "function", "function": {"name": ..., "arguments": ...}}`` and
    the single-key ``{"search_kb": {...}}`` form.
    """
    inner = payload.get("function")
    if isinstance(inner, dict):
        payload = inner

    for key in _NAME_KEYS:
        name = payload.get(key)
        if isinstance(name, str) and name:
            arguments: Any = None
            for arg_key in _ARG_KEYS:
                if arg_key in payload:
                    arguments = payload[arg_key]
                    break
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = None
            return name, arguments if isinstance(arguments, dict) else {}

    # {"search_kb": {"query": "..."}} - a single tool name as the only key.
    if len(payload) == 1:
        (name, arguments), = payload.items()
        if isinstance(name, str) and isinstance(arguments, dict):
            return name, arguments

    return None, None


def _name_from_prefix(prefix: str, known_tools: set[str]) -> str | None:
    """Find a registered tool named in the text just before the JSON.

    Handles ``create_ticket\\n{"summary": ...}``, where the model writes the
    tool name as a bare label and the JSON holds only the arguments.
    """
    if not prefix:
        return None
    tail = prefix[-120:].lower()
    named = [t for t in known_tools if t.lower() in tail]
    # Ambiguous (two tools named) or none: do not guess.
    return named[0] if len(named) == 1 else None


def parse_text_tool_calls(content: str, known_tools: set[str]) -> list[ToolCall]:
    """Return tool calls that ``content`` was clearly trying to express.

    An empty list means "this is just an answer", which is the common case and
    always the safe default.
    """
    payload, prefix = _extract_object(content)
    if payload is None:
        return []

    name, arguments = _unpack(payload)
    if not name or name not in known_tools:
        # The JSON carried only arguments; fall back to a tool named just
        # before it.
        name = _name_from_prefix(prefix, known_tools)
        if name is None:
            return []
        arguments = payload

    return [ToolCall(id="call_recovered_0", name=name, arguments=arguments or {})]
