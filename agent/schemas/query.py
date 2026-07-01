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


class QueryResponse(BaseModel):
    """The full result of one question."""

    answer: str
    sources: list[str] = Field(default_factory=list)
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
