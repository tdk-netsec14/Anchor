"""End-to-end agent loop, driven by a scripted provider.

These exercise the real orchestration - retrieval -> prompt -> tool loop ->
output guardrails -> response - with the network and the embedding model
replaced by test doubles. Provider HTTP behaviour is covered separately in
test_providers.py, and the whole stack against a live Ollama by running the
service (see the README demo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.agent import AnchorAgent
from agent.routing.router import ModelRouter
from agent.tools.registry import ToolRegistry
from tests.fakes import FakeRetriever, ScriptedProvider, text_response, tool_response

pytestmark = pytest.mark.integration


def make_registry(retriever=None, tickets_dir=None) -> ToolRegistry:
    """Build the production tool set, with tickets redirected away from data/."""
    from agent.routers.query import build_tool_registry

    return build_tool_registry(retriever, tickets_dir)


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    return make_registry(tickets_dir=tmp_path / "tickets")


def build_agent(
    provider: ScriptedProvider, registry: ToolRegistry, retriever=None
) -> AnchorAgent:
    """Wire the agent with a scripted provider and no real embedding model."""
    retriever = retriever if retriever is not None else FakeRetriever()
    router = ModelRouter(provider_classes={})
    router.register("fake", provider)
    return AnchorAgent(router=router, registry=registry, retriever=retriever)


# --------------------------------------------------------------------------
# Grounded answers
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grounded_answer_returns_sources(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("You get 25 days of paid annual leave per year [S1].")]
    )
    outcome = await build_agent(provider, registry).answer("How many days of annual leave?")

    assert "25 days" in outcome.answer
    assert outcome.sources
    assert "hr_leave_policy.pdf" in " ".join(outcome.sources)
    assert outcome.tokens_used > 0
    assert outcome.latency_ms > 0


@pytest.mark.asyncio
async def test_retrieved_context_is_passed_to_the_model(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(script=[text_response("25 days [S1].")])
    await build_agent(provider, registry).answer("How many days of annual leave?")

    sent = provider.calls[0]
    assert sent["tools"] == ["search_kb", "calculator", "create_ticket"]
    context_turns = [
        m.content
        for m in sent["messages"]
        if m.role == "user" and "[S1]" in m.content
    ]
    assert context_turns, "the [S1]-tagged context block was not sent"
    assert "25 days" in context_turns[0]


@pytest.mark.asyncio
async def test_system_prompt_is_sent_but_never_echoed(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(script=[text_response("25 days [S1].")])
    outcome = await build_agent(provider, registry).answer("How many days of annual leave?")

    assert "25 days" in outcome.answer
    assert "You are Anchor" not in outcome.answer
    assert "Rules:" not in outcome.answer


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_calculator_tool_is_invoked_and_recorded(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("calculator", {"expression": "1250 * 0.15"}),
            text_response("15% of 1250 is 187.5 [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("Calculate 15 percent of 1250")

    assert [t.name for t in outcome.tool_calls] == ["calculator"]
    assert outcome.tool_calls[0].ok is True
    assert "187.5" in outcome.tool_calls[0].result_preview
    assert "187.5" in outcome.answer


@pytest.mark.asyncio
async def test_tool_result_is_fed_back_to_the_model(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("calculator", {"expression": "2+2"}),
            text_response("4 [S1]."),
        ]
    )
    await build_agent(provider, registry).answer("What is 2+2?")

    second = provider.calls[1]
    tool_messages = [m for m in second["messages"] if m.role == "tool"]
    assert tool_messages, "tool result was not fed back"
    assert "4 (from: 2+2)" in tool_messages[0].content


@pytest.mark.asyncio
async def test_invalid_tool_call_is_reported_to_the_model_not_raised(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("calculator", {"expression": "__import__('os').system('id')"}),
            text_response("I cannot run that expression [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("Run this for me")

    assert outcome.tool_calls[0].ok is False
    assert "allowed" in outcome.tool_calls[0].result_preview.lower()
    # The request still completes with an answer.
    assert outcome.answer


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_to_the_model(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("rm_rf", {}),
            text_response("That tool does not exist [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("Delete everything")

    assert outcome.tool_calls[0].name == "rm_rf"
    assert outcome.tool_calls[0].ok is False
    assert "Unknown tool" in outcome.tool_calls[0].result_preview


@pytest.mark.asyncio
async def test_create_ticket_writes_a_file(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("create_ticket", {"summary": "VPN drops every hour", "priority": "high"}),
            text_response("I have created a ticket for you [S1]."),
        ]
    )
    outcome = await build_agent(registry=registry, provider=provider).answer(
        "My VPN keeps dropping, please escalate"
    )

    call = outcome.tool_calls[0]
    assert call.name == "create_ticket"
    assert call.ok is True
    assert "TCK-" in call.result_preview

    tickets = list(registry.get("create_ticket").tickets_dir.glob("*.json"))
    assert len(tickets) == 1
    record = json.loads(tickets[0].read_text())
    assert record["priority"] == "high"
    assert record["simulated"] is True
    assert "VPN" in record["summary"]


@pytest.mark.asyncio
async def test_create_ticket_rejects_an_invalid_priority(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("create_ticket", {"summary": "Cannot print", "priority": "catastrophic"}),
            text_response("That priority is not valid [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("Escalate this")

    assert outcome.tool_calls[0].ok is False
    assert "priority" in outcome.tool_calls[0].result_preview


@pytest.mark.asyncio
async def test_search_kb_tool_adds_its_sources_to_the_response(registry: ToolRegistry) -> None:
    retriever = FakeRetriever()
    shared = make_registry(retriever)  # tool and agent share one retriever
    provider = ScriptedProvider(
        script=[
            tool_response("search_kb", {"query": "expense receipts", "top_k": 2}),
            text_response("Receipts are required over 25 USD [S1]."),
        ]
    )
    outcome = await build_agent(provider, shared, retriever).answer(
        "Do I need a receipt for a 30 USD lunch?"
    )

    assert outcome.tool_calls[0].name == "search_kb"
    assert outcome.tool_calls[0].ok is True
    # The tool performed its own retrieval through the shared retriever.
    assert "expense receipts" in retriever.calls
    # Whatever it found reaches the caller, de-duplicated against the chunks the
    # first retrieval already returned. The fake corpus holds two chunks, so the
    # tool's result is the same pair and the list is unchanged.
    expected = [c.citation for c in retriever.chunks]
    assert outcome.sources == expected
    assert len(outcome.sources) == len(set(outcome.sources))


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_turn_are_all_executed(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            tool_response("calculator", {"expression": "30*3"}, call_id="c1"),
            tool_response("create_ticket", {"summary": "Three missing items", "priority": "low"}, call_id="c2"),
            text_response("That is 90, and I have raised a ticket [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("3 items at 30 each, and escalate")

    assert [t.name for t in outcome.tool_calls] == ["calculator", "create_ticket"]
    assert all(t.ok for t in outcome.tool_calls)


@pytest.mark.asyncio
async def test_tool_iteration_budget_is_enforced(registry: ToolRegistry) -> None:
    """A model that only ever calls tools must not loop forever."""
    from agent.config import get_settings

    provider = ScriptedProvider(
        script=[tool_response("calculator", {"expression": "1+1"})] * 10
    )
    outcome = await build_agent(provider, registry).answer("loop forever")

    limit = get_settings().ROUTER_MAX_TOOL_ITERATIONS
    assert len(provider.calls) <= limit
    assert "tool_iteration_limit_reached" in outcome.guardrail_flags


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pii_in_the_answer_is_redacted(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("Email alice.smith@corp.example or call 555-123-4567 [S1].")]
    )
    outcome = await build_agent(provider, registry).answer("How do I contact support?")

    assert "alice.smith@corp.example" not in outcome.answer
    assert "555-123-4567" not in outcome.answer
    assert "output_pii_redacted" in outcome.guardrail_flags


@pytest.mark.asyncio
async def test_fabricated_source_is_flagged(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("Per [S9] the limit is 5 days.")]
    )
    outcome = await build_agent(provider, registry).answer("How many carry-over days?")

    assert "output_unsupported_source" in outcome.guardrail_flags


@pytest.mark.asyncio
async def test_malformed_output_is_retried_once(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[
            text_response("the the the the the the the the the the the the"),
            text_response("A well-formed answer with enough distinct words [S1]."),
        ]
    )
    outcome = await build_agent(provider, registry).answer("What is the policy?")

    assert len(provider.calls) == 2
    assert "output_retry_after_malformed" in outcome.guardrail_flags
    assert "well-formed" in outcome.answer


@pytest.mark.asyncio
async def test_unrecoverable_output_is_replaced_with_a_safe_message(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("the the the the the the the the the the the the")] * 4
    )
    outcome = await build_agent(provider, registry).answer("What is the policy?")

    assert "output_malformed_unrecoverable" in outcome.guardrail_flags
    assert "could not produce a well-formed answer" in outcome.answer.lower()


@pytest.mark.asyncio
async def test_empty_retrieval_produces_a_grounded_refusal(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("The knowledge base has no information on this topic.")]
    )
    outcome = await build_agent(provider, registry, FakeRetriever(chunks=[])).answer(
        "What is the airspeed velocity of an unladen swallow?"
    )

    assert outcome.retrieval_hits == 0
    assert outcome.answer
    # No sources were retrieved, so none may be claimed.
    assert "output_unsupported_source" not in outcome.guardrail_flags


# --------------------------------------------------------------------------
# Provider failures
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_providers_failing_raises_all_providers_failed(registry: ToolRegistry) -> None:
    from agent.routing.router import AllProvidersFailed

    provider = ScriptedProvider(fail_times=99)
    with pytest.raises(AllProvidersFailed):
        await build_agent(provider, registry).answer("Anything")


@pytest.mark.asyncio
async def test_forced_model_is_reported_in_the_outcome(registry: ToolRegistry) -> None:
    provider = ScriptedProvider(
        script=[text_response("25 days [S1].", model="pinned-model")], model="pinned-model"
    )
    outcome = await build_agent(provider, registry).answer(
        "How many days of annual leave?", force_model="fake"
    )
    assert outcome.model_used == "fake/pinned-model"
    assert "forced" in outcome.routing_reason


@pytest.mark.asyncio
async def test_truncated_generation_is_retried_and_replaced(registry: ToolRegistry) -> None:
    """A generation cut off mid-sentence must not be shipped to the user."""
    from agent.routing.providers.base import LLMResponse

    provider = ScriptedProvider(
        script=[
            LLMResponse(
                content="A long and varied answer that reads like prose and has many "
                "distinct words in it, but which the provider cut off",
                model="fake-model",
                provider="fake",
                finish_reason="length",
            ),
            LLMResponse(
                content="A long and varied answer that reads like prose and has many "
                "distinct words in it, but which the provider cut off again",
                model="fake-model",
                provider="fake",
                finish_reason="length",
            ),
        ]
    )
    outcome = await build_agent(provider, registry).answer("What is the policy?")

    # Both attempts were truncated, so a safe message replaces the fragment.
    assert "output_truncated" in outcome.guardrail_flags
    assert "output_malformed_unrecoverable" in outcome.guardrail_flags
    assert "cut off" not in outcome.answer
    assert "could not produce a well-formed answer" in outcome.answer.lower()


@pytest.mark.asyncio
async def test_sources_are_deduplicated(registry: ToolRegistry) -> None:
    """search_kb re-surfaces chunks the first retrieval already returned."""
    from agent.routers.query import build_tool_registry

    retriever = FakeRetriever()
    shared = build_tool_registry(retriever)
    provider = ScriptedProvider(
        script=[
            tool_response("search_kb", {"query": "annual leave days", "top_k": 3}),
            text_response("You get 25 days [S1]."),
        ]
    )
    outcome = await build_agent(provider, shared, retriever).answer("How many days of leave?")

    assert outcome.tool_calls[0].name == "search_kb"
    assert len(outcome.sources) == len(set(outcome.sources)), "duplicate sources returned"
