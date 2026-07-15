"""Test doubles for LLM providers and the retrieval stack.

A scripted provider keeps the agent-loop tests fast, deterministic and
independent of a running Ollama, which is what makes them usable in CI. The
*real* providers are exercised separately by the integration tests and by
running the service against a live Ollama - see ``docs/architecture.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent.rag.retriever import RetrievalResult
from agent.routing.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderError,
    ProviderTimeout,
    ToolCall,
    ToolSpec,
)
from ingestion.vector_store import RetrievedChunk


class ScriptedProvider(LLMProvider):
    """A provider that replays a fixed script and records what it was asked."""

    name = "fake"

    def __init__(
        self,
        script: Sequence[LLMResponse] | None = None,
        *,
        model: str = "fake-model",
        fail_times: int = 0,
        error: ProviderError | None = None,
        configured: bool = True,
    ) -> None:
        super().__init__(model)
        self.script = list(script or [])
        self.fail_times = fail_times
        self.error = error or ProviderTimeout(f"{self.name} timed out", provider=self.name)
        self._configured = configured
        #: Every (messages, tools) pair the router sent, in order.
        self.calls: list[dict] = []

    def is_configured(self) -> bool:
        return self._configured

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [t.name for t in (tools or [])],
                "system": next((m.content for m in messages if m.role == "system"), None),
            }
        )
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.error

        if self.script:
            index = min(len(self.calls) - 1, len(self.script) - 1)
            return self.script[index]

        return LLMResponse(
            content="No script configured.",
            model=self.model,
            provider=self.name,
            prompt_tokens=10,
            completion_tokens=5,
        )


def text_response(content: str, *, model: str = "fake-model", provider: str = "fake") -> LLMResponse:
    return LLMResponse(
        content=content, model=model, provider=provider, prompt_tokens=100, completion_tokens=25
    )


def tool_response(
    name: str,
    arguments: dict,
    *,
    call_id: str = "call_0",
    content: str = "",
    model: str = "fake-model",
    provider: str = "fake",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        model=model,
        provider=provider,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        prompt_tokens=80,
        completion_tokens=10,
    )


class FakeRetriever:
    """Returns canned chunks, so agent-loop tests need no embedding model."""

    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self.chunks = chunks if chunks is not None else default_chunks()
        self.calls: list[str] = []

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        min_score: float = 0.0,
        doc_name: str | None = None,
    ) -> RetrievalResult:
        self.calls.append(query)
        selected = self.chunks[: (top_k or 4)]
        if doc_name:
            selected = [c for c in selected if c.doc_name == doc_name]
        return RetrievalResult(
            query=query,
            chunks=selected,
            top_score=selected[0].score if selected else 0.0,
        )


def default_chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id="hr_leave_policy.pdf#p1#c0",
            doc_name="hr_leave_policy.pdf",
            page_number=1,
            text=(
                "Full-time employees accrue 25 days of paid annual leave per year, "
                "accruing monthly at 2.08 days for each completed month of service."
            ),
            distance=0.35,
        ),
        RetrievedChunk(
            chunk_id="it_support_policy.pdf#p1#c1",
            doc_name="it_support_policy.pdf",
            page_number=1,
            text="A P1 incident receives a first response within 30 minutes.",
            distance=0.55,
        ),
    ]
