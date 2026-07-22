"""Request and response models for ``POST /query``."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """A user question."""

    query: str = Field(
        min_length=1,
        max_length=10_000,
        description="The user's natural-language question.",
        examples=["How many days of annual leave do I get?"],
    )
    session_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional caller-supplied conversation id, for client-side history.",
    )
    conversation_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Anchor conversation to append this turn to. The question and the "
            "answer, with the model, latency, tokens, cost, sources and tool "
            "calls that produced it, are stored against it. Must be a "
            "conversation in the caller's own workspace; anything else is a 404."
        ),
    )
    force_model: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Bypass routing and use this model, as 'provider' or "
            "'provider/model' (e.g. 'groq', 'ollama/llama3.2:3b'). Intended for "
            "testing and evaluation."
        ),
    )


class ToolCallRecord(BaseModel):
    """One tool invocation, as reported back to the caller."""

    name: str
    ok: bool = True
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_preview: str = Field(
        default="", description="Truncated tool output; full text is not returned."
    )
    latency_ms: float = 0.0


class SourceDetail(BaseModel):
    """One retrieved chunk, with the provenance a UI needs to render a citation.

    ``sources`` remains the flat citation strings the output guardrail verifies
    against; this is the same information in structured form, for display. A
    chunk surfaced later by the ``search_kb`` tool carries no excerpt, so it
    appears in ``sources`` only.
    """

    citation: str
    doc_name: str
    page_number: int | None = None
    chunk_id: str = ""
    score: float = 0.0
    excerpt: str = Field(default="", description="Leading text of the chunk, truncated.")


class QueryResponse(BaseModel):
    """The full result of one question."""

    answer: str
    sources: list[str] = Field(default_factory=list)
    source_details: list[SourceDetail] = Field(
        default_factory=list,
        description="Structured citations, parallel to `sources` where available.",
    )
    model_used: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    latency_ms: float
    tokens_used: int
    estimated_cost_usd: float
    guardrail_flags: list[str] = Field(default_factory=list)
    request_id: str
    # Observability detail beyond the required contract.
    provider: str = ""
    routing_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    fallbacks: list[dict[str, str]] = Field(default_factory=list)
    session_id: str | None = None
