"""The tool-calling agent loop.

Retrieval is not a fixed prologue here: the model is told what it has, given the
tool schemas, and may spend up to ``ROUTER_MAX_TOOL_ITERATIONS`` round trips
pulling more context before answering. Every tool call is recorded, validated
and fed back; every failure becomes a message the model can recover from rather
than an exception.

This is the only place that knows the *order* of the steps. Providers know how to
talk HTTP, tools know how to do one thing, and the guardrails know what to
refuse - none of them orchestrate.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from starlette.concurrency import run_in_threadpool

from agent import prompts
from agent.config import get_settings
from agent.guardrails import output_guard
from agent.observability.logger import get_logger
from agent.rag.retriever import RetrievalResult, Retriever, build_context
from agent.routing.providers.base import Message, ProviderError, ToolCall
from agent.routing.router import AllProvidersFailed, ModelRouter, RoutingDecision
from agent.schemas.query import SourceDetail, ToolCallRecord
from agent.tools.parsing import parse_text_tool_calls
from agent.tools.registry import ToolContext, ToolRegistry

log = get_logger(__name__)

#: How much of a tool result is echoed back to the caller. Enough to show what
#: happened without shipping a whole document through the API.
RESULT_PREVIEW_CHARS = 300


@dataclass(slots=True)
class AgentOutcome:
    """Everything one question produced, ready to be shaped into a response."""

    answer: str
    sources: list[str] = field(default_factory=list)
    source_details: list[SourceDetail] = field(default_factory=list)
    #: ``(label, value)`` pairs a tool declared as quotable references.
    tool_references: list[tuple[str, str]] = field(default_factory=list)
    allowed_source_tags: set[str] = field(default_factory=set)
    model_used: str = ""
    provider: str = ""
    routing_reason: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    guardrail_flags: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    latency_ms: float = 0.0
    retrieval_hits: int = 0
    top_score: float = 0.0
    #: Provider finish reason for the final generation ("stop", "length", ...).
    finish_reason: str = "stop"

    @property
    def tokens_used(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AnchorAgent:
    """Answers one question end to end."""

    def __init__(
        self,
        router: ModelRouter,
        registry: ToolRegistry,
        retriever: Retriever | None = None,
    ) -> None:
        self.router = router
        self.tools = registry
        self.retriever = retriever or Retriever()

    # -- public API --------------------------------------------------------
    async def answer(
        self,
        query: str,
        *,
        force_model: str | None = None,
        workspace_id: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> AgentOutcome:
        """Answer one question inside one workspace.

        ``workspace_id`` scopes retrieval. It is optional only because the
        development path has no tenancy at all; a deployment with a database
        refuses an unscoped search inside the retriever.
        """
        settings = get_settings()
        started = time.perf_counter()
        context = tool_context or ToolContext(workspace_id=workspace_id)

        decision = self.router.route(query, force_model=force_model)
        log.info(
            "agent.routed",
            context={
                "model": decision.model_id,
                "tier": decision.tier,
                "reason": decision.reason,
                "forced": decision.forced,
                "workspace_id": context.workspace_id,
            },
        )

        # Retrieval embeds the query and reads the store, and the tools re-enter
        # the same path. Both are synchronous and CPU-bound, so they run in a
        # worker thread rather than on the event loop.
        result = await run_in_threadpool(
            partial(self.retriever.retrieve, workspace_id=context.workspace_id), query
        )
        context_block = build_context(result.chunks)
        allowed_tags = self._allowed_source_tags(result)

        outcome = AgentOutcome(
            answer="",
            sources=[c.citation for c in result.chunks],
            source_details=[
                SourceDetail(
                    citation=c.citation,
                    doc_name=c.doc_name,
                    page_number=c.page_number,
                    chunk_id=c.chunk_id,
                    score=c.score,
                    excerpt=c.text[:RESULT_PREVIEW_CHARS].strip(),
                )
                for c in result.chunks
            ],
            allowed_source_tags=allowed_tags,
            model_used=decision.model_id,
            provider=decision.provider,
            routing_reason=decision.reason,
            retrieval_hits=len(result.chunks),
            top_score=result.top_score,
        )

        messages = self._initial_messages(query, context_block)
        specs = self.tools.specs()
        last_content = ""

        # -- tool loop ----------------------------------------------------
        for iteration in range(settings.ROUTER_MAX_TOOL_ITERATIONS):
            provider_result = await self.router.execute(messages, decision, tools=specs)
            response = provider_result.response
            outcome.model_used = f"{provider_result.decision.provider}/{response.model}"
            outcome.provider = provider_result.decision.provider
            outcome.routing_reason = provider_result.decision.reason
            outcome.fallbacks = provider_result.fallbacks
            outcome.prompt_tokens += response.prompt_tokens
            outcome.completion_tokens += response.completion_tokens
            outcome.cost_usd += self.router.get_provider(
                provider_result.decision.provider, response.model
            ).cost_estimate(response.prompt_tokens, response.completion_tokens)

            # A model can express a tool call in three ways: the provider's
            # tool channel, plain text, or truncated text. Recover the second
            # case and let the third fall through to the malformed-output
            # retry in _finalise.
            effective_tool_calls = response.tool_calls
            if not effective_tool_calls and response.finish_reason != "length":
                recovered = parse_text_tool_calls(response.content, set(self.tools.names))
                if recovered:
                    log.info(
                        "agent.tool_call_recovered_from_text",
                        context={"tool": recovered[0].name},
                    )
                    effective_tool_calls = recovered

            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=effective_tool_calls,
                )
            )

            outcome.finish_reason = response.finish_reason
            if not effective_tool_calls:
                outcome.answer = response.content
                break
            last_content = response.content

            # -- execute requested tools ---------------------------------
            await self._run_tool_round(effective_tool_calls, messages, outcome, context)

            log.info(
                "agent.tool_iteration",
                context={
                    "iteration": iteration + 1,
                    "tools": [t.name for t in effective_tool_calls],
                },
            )
        else:
            # Budget exhausted while the model was still calling tools. Keep any
            # prose it produced on the way, otherwise tell the user plainly.
            if last_content.strip():
                outcome.answer = last_content
            else:
                outcome.answer = (
                    "I could not complete this request within the allowed number "
                    "of tool calls. Please rephrase, or create a support ticket."
                )
            outcome.guardrail_flags.append("tool_iteration_limit_reached")

        outcome = await self._finalise(outcome, messages, decision)
        # A `search_kb` call usually re-surfaces chunks the first retrieval
        # already returned; dedupe while preserving order.
        outcome.sources = list(dict.fromkeys(outcome.sources))
        outcome.latency_ms = (time.perf_counter() - started) * 1000
        return outcome

    # -- internals ---------------------------------------------------------
    async def _run_tool_round(
        self,
        calls: list[ToolCall],
        messages: list[Message],
        outcome: AgentOutcome,
        context: ToolContext,
    ) -> None:
        """Execute one round of tool calls and feed the results back.

        Failures are fed back verbatim rather than swallowed: the model can see
        what went wrong and retry with corrected arguments, which is the whole
        point of the registry returning a reason instead of raising.
        """
        for call in calls:
            result = await run_in_threadpool(partial(self.tools.execute, context=context), call)
            outcome.tool_calls.append(
                ToolCallRecord(
                    name=call.name,
                    ok=result.ok,
                    arguments=_safe_arguments(call.arguments),
                    result_preview=result.content[:RESULT_PREVIEW_CHARS],
                    latency_ms=result.latency_ms,
                )
            )
            # Captured here, from the full result, rather than parsed back out
            # of the truncated preview the response carries.
            outcome.tool_references.extend(self._references_from(call.name, result.content))
            messages.append(
                Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=call.id,
                    name=call.name,
                )
            )
            if call.name == "search_kb":
                outcome.sources.extend(result.metadata.get("citations", []))

    def _initial_messages(self, query: str, context_block: str) -> list[Message]:
        """Build the opening turn.

        The user query is its own message and the context is clearly fenced.
        Keeping retrieved text out of the system message means a poisoned
        document cannot masquerade as an instruction.
        """
        if context_block:
            context_turn = (
                f"CONTEXT retrieved from the knowledge base:\n\n{context_block}\n\n"
                "Answer the user's question using only this context, citing the "
                "[S1], [S2] ... tags."
            )
        else:
            context_turn = f"{prompts.NO_CONTEXT_NOTICE}\n\n{prompts.refusal_prompt()}"
        return [
            Message(role="system", content=prompts.system_prompt()),
            Message(role="user", content=context_turn),
            Message(role="user", content=query),
        ]

    @staticmethod
    def _allowed_source_tags(result: RetrievalResult) -> set[str]:
        tags = {f"S{i}" for i in range(1, len(result.chunks) + 1)}
        tags.update(c.doc_name for c in result.chunks)
        return tags

    async def _finalise(
        self,
        outcome: AgentOutcome,
        messages: list[Message],
        decision: RoutingDecision,
    ) -> AgentOutcome:
        """Apply output guardrails, retrying once if the answer is malformed."""
        # `require_citation` is deliberately left off. It flags any answer that
        # cites nothing, which includes a correct refusal - and refusing to cite
        # is the right behaviour when the model declines to answer. Turning it
        # on made it fire on three of four requests, so it stays available for a
        # deployment that wants the stricter reading but is not on by default.
        check = output_guard.inspect_output(
            outcome.answer, allowed_sources=outcome.allowed_source_tags
        )

        # A generation cut off mid-sentence (or mid-JSON) is malformed even
        # though the fragment may look like text. Small models hit this often
        # when a tool call runs past the token budget.
        truncated = outcome.finish_reason == "length"
        if truncated and "output_truncated" not in outcome.guardrail_flags:
            outcome.guardrail_flags.append("output_truncated")

        if truncated or not output_guard.looks_structured(check.text):
            log.warning(
                "output.malformed",
                context={"length": len(outcome.answer or "")},
            )
            correction = output_guard.build_format_retry_instruction()
            retry_messages = [
                Message(
                    role="system" if m.role == "system" else "user",
                    content=m.content,
                )
                for m in messages
                if m.role in ("system", "user")
            ]
            retry_messages.append(
                Message(
                    role="assistant",
                    content=prompts.format_retry_prompt(correction),
                )
            )
            try:
                retried = await self.router.execute(retry_messages, decision)
            except (ProviderError, AllProvidersFailed) as exc:
                # The original answer is still returned; a failed formatting
                # retry must not turn a 200 into an error.
                log.error("output.retry_failed", context={"error_type": type(exc).__name__})
            else:
                outcome.prompt_tokens += retried.response.prompt_tokens
                outcome.completion_tokens += retried.response.completion_tokens
                outcome.answer = retried.response.content
                outcome.finish_reason = retried.response.finish_reason
                outcome.guardrail_flags.append("output_retry_after_malformed")
                truncated = outcome.finish_reason == "length"
                check = output_guard.inspect_output(
                    outcome.answer, allowed_sources=outcome.allowed_source_tags
                )

        if truncated or not output_guard.looks_structured(check.text):
            # The retry did not help either - it was still truncated, or still
            # malformed. Swap in a safe message *through `check`* so it still
            # flows through the redaction and flagging below, rather than being
            # overwritten by the next assignment.
            log.warning(
                "output.malformed_unrecoverable",
                context={"truncated": truncated},
            )
            outcome.guardrail_flags.append("output_malformed_unrecoverable")
            check.text = output_guard.MALFORMED_ANSWER_MESSAGE
            check.well_formed = False

        outcome.answer = check.text
        for flag in check.flags:
            if flag not in outcome.guardrail_flags:
                outcome.guardrail_flags.append(flag)
        if check.unsupported_sources:
            outcome.guardrail_flags.append("output_unsupported_source")

        # Last, so it runs over the final text: a tool that minted a
        # quotable identifier (a ticket id) has its reference guaranteed to
        # reach the caller, and the repair is flagged rather than silent.
        if outcome.tool_references:
            outcome.answer, surfaced = output_guard.surface_missing_references(
                outcome.answer, outcome.tool_references
            )
            if surfaced:
                outcome.guardrail_flags.append(output_guard.REFERENCE_SURFACED_FLAG)

        return outcome

    def _references_from(self, tool_name: str, content: str) -> list[tuple[str, str]]:
        """Quotable references declared by a tool that just ran.

        Reads the tool's own declaration rather than the response, so a tool
        opts in by setting `reference_pattern` and a tool that did not run can
        never contribute one.
        """
        tool = self.tools.get(tool_name)
        pattern = getattr(tool, "reference_pattern", None)
        if not pattern:
            return []
        label = getattr(tool, "reference_label", "Reference")
        # de-duplicated, order preserved: several references for one call are
        # surfaced once each.
        return list(dict.fromkeys((label, match) for match in re.findall(pattern, content)))


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Trim tool arguments before they are echoed into the response."""
    cleaned: dict[str, Any] = {}
    for key, value in list(arguments.items())[:10]:
        if isinstance(value, str) and len(value) > 300:
            cleaned[key] = value[:300] + "..."
        else:
            cleaned[key] = value
    return cleaned
