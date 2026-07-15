"""Recovery of tool calls that small models emit as text rather than as a call.

These shapes were all observed in real runs against a local 3B model.
"""

from __future__ import annotations

import pytest

from agent.tools.parsing import parse_text_tool_calls

KNOWN = {"search_kb", "calculator", "create_ticket"}


class TestRecovery:
    @pytest.mark.parametrize(
        ("content", "tool", "args"),
        [
            (
                '{"type":"function","function":{"name":"calculator",'
                '"arguments":{"expression":"2+2"}}}',
                "calculator",
                {"expression": "2+2"},
            ),
            (
                '{"name":"search_kb","arguments":{"query":"leave policy"}}',
                "search_kb",
                {"query": "leave policy"},
            ),
            (
                '{"name":"create_ticket","parameters":{"summary":"broken laptop",'
                '"priority":"high"}}',
                "create_ticket",
                {"summary": "broken laptop", "priority": "high"},
            ),
            (
                '```json\n{"name":"calculator","arguments":{"expression":"6*7"}}\n```',
                "calculator",
                {"expression": "6*7"},
            ),
        ],
    )
    def test_recognises_the_common_shapes(
        self, content: str, tool: str, args: dict
    ) -> None:
        calls = parse_text_tool_calls(content, KNOWN)
        assert len(calls) == 1
        assert calls[0].name == tool
        assert calls[0].arguments == args

    @pytest.mark.parametrize(
        ("content", "tool", "args"),
        [
            # A bare tool name on the first line, the call on the next.
            (
                'create_ticket\n{"summary":"a real problem", "priority":"low"}',
                "create_ticket",
                {"summary": "a real problem", "priority": "low"},
            ),
            # Prose announcing the call, then the JSON.
            (
                'I will use the "search_kb" function to look this up.\n\n'
                '{"name":"search_kb","parameters":{"query":"2019 world cup","top_k":1}}',
                "search_kb",
                {"query": "2019 world cup", "top_k": 1},
            ),
        ],
    )
    def test_recovers_prose_then_json(
        self, content: str, tool: str, args: dict
    ) -> None:
        """Small models wrap the call in commentary; that must still count."""
        calls = parse_text_tool_calls(content, KNOWN)
        assert len(calls) == 1
        assert calls[0].name == tool
        assert calls[0].arguments == args

    def test_single_key_form_is_recognised(self) -> None:
        calls = parse_text_tool_calls('{"calculator": {"expression": "1+1"}}', KNOWN)
        assert calls and calls[0].name == "calculator"
        assert calls[0].arguments == {"expression": "1+1"}

    def test_string_encoded_arguments_are_parsed(self) -> None:
        calls = parse_text_tool_calls(
            '{"name":"calculator","arguments":"{\\"expression\\": \\"3+4\\"}"}', KNOWN
        )
        assert calls[0].arguments == {"expression": "3+4"}

    def test_malformed_arguments_become_an_empty_dict_for_the_registry_to_reject(
        self,
    ) -> None:
        calls = parse_text_tool_calls('{"name":"calculator","arguments":"{oops"}', KNOWN)
        assert calls and calls[0].arguments == {}


class TestNoFalsePositives:
    """A false positive would execute a tool the model never asked for."""

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "   ",
            "You get 25 days of annual leave per year [S1].",
            "The answer is {\"days\": 25}.",
            # A tool that is not registered must never be inferred.
            '{"name":"delete_everything","arguments":{}}',
            '{"type":"function","function":{"name":"rm_rf","arguments":{}}}',
            '{"calculator": {"expression": "1+1"}, "note": "extra"}',
            "```\nnot json at all\n```",
            '{"name":"calculator"',  # truncated
        ],
    )
    def test_non_tool_content_is_left_alone(self, content: str) -> None:
        assert parse_text_tool_calls(content, KNOWN) == []

    def test_ordinary_json_answer_is_not_a_tool_call(self) -> None:
        answer = '{"days": 25, "accrual": 2.08, "policy": "hr_leave_policy.pdf"}'
        assert parse_text_tool_calls(answer, KNOWN) == []

    def test_registered_name_alone_is_not_enough(self) -> None:
        """The arguments must be an object, not a bare mention of the tool."""
        assert parse_text_tool_calls('{"tool": "calculator", "result": 42}', KNOWN) == []
