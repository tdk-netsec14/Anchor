"""Tool registry: dispatch, argument validation and failure containment."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from agent.routing.providers.base import ToolCall
from agent.tools.registry import Tool, ToolRegistry, ToolResult


class EchoArgs(BaseModel):
    text: str = Field(min_length=1, max_length=50)
    times: int = Field(default=1, ge=1, le=3)


class EchoTool(Tool):
    name = "echo"
    description = "Echo text."
    args_model = EchoArgs

    def run(self, context, **kwargs):
        return kwargs["text"] * kwargs["times"]


class ExplodingTool(Tool):
    name = "boom"
    description = "Always raises."

    def run(self, context, **kwargs):
        raise RuntimeError("internal detail: /etc/secret-path")


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all([EchoTool(), ExplodingTool()])
    return reg


def test_registration_and_lookup(registry: ToolRegistry) -> None:
    assert registry.names == ["boom", "echo"]
    assert registry.get("echo") is not None
    assert registry.get("missing") is None


def test_duplicate_registration_is_rejected(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_specs_are_valid_json_schema(registry: ToolRegistry) -> None:
    specs = {s.name: s for s in registry.specs()}
    assert set(specs) == {"echo", "boom"}
    echo = specs["echo"]
    assert echo.description == "Echo text."
    assert echo.parameters["type"] == "object"
    assert "text" in echo.parameters["properties"]


def test_execute_returns_result(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="echo", arguments={"text": "ab", "times": 2}))
    assert result.ok is True
    assert result.content == "abab"
    assert result.name == "echo"


def test_arguments_may_arrive_as_a_json_string(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="echo", arguments='{"text": "hi"}'))
    assert result.ok is True
    assert result.content == "hi"


def test_missing_required_argument_is_reported_not_raised(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="echo", arguments={}))
    assert result.ok is False
    assert "Invalid arguments" in result.content


def test_out_of_range_argument_is_rejected(registry: ToolRegistry) -> None:
    result = registry.execute(
        ToolCall(id="1", name="echo", arguments={"text": "a", "times": 99})
    )
    assert result.ok is False
    assert "times" in result.content


def test_malformed_json_arguments_are_reported(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="echo", arguments="{not json"))
    assert result.ok is False
    assert "not valid JSON" in result.content


def test_unknown_tool_is_reported_and_lists_alternatives(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="delete_everything", arguments={}))
    assert result.ok is False
    assert "Unknown tool" in result.content
    assert "echo" in result.content


def test_tool_exception_is_contained_and_detail_is_not_leaked(registry: ToolRegistry) -> None:
    result = registry.execute(ToolCall(id="1", name="boom", arguments={}))
    assert result.ok is False
    # The type is safe to show; the message may contain internal paths.
    assert "RuntimeError" in result.content
    assert "/etc/secret-path" not in result.content


def test_tool_returning_tool_result_preserves_metadata() -> None:
    class MetaTool(Tool):
        name = "meta"
        description = "Returns metadata."

        def run(self, context, **kwargs):
            return ToolResult(name="meta", ok=True, content="body", metadata={"citations": ["a.pdf"]})

    reg = ToolRegistry()
    reg.register(MetaTool())
    result = reg.execute(ToolCall(id="1", name="meta", arguments={}))
    assert result.ok is True
    assert result.content == "body"
    assert result.metadata["citations"] == ["a.pdf"]


def test_every_anchor_tool_declares_a_schema() -> None:
    """The agent loop hands these straight to the model, so they must be valid."""
    from agent.routers.query import build_tool_registry

    specs = build_tool_registry().specs()
    assert {s.name for s in specs} == {"search_kb", "calculator", "create_ticket"}
    for spec in specs:
        assert spec.description
        assert spec.parameters["type"] == "object"
