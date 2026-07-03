"""Provider-independent LLM interface.

Every provider implements :class:`LLMProvider`, so the router, the agent loop
and the evaluation harness never import a vendor SDK or know a vendor URL. The
agent loop hands over an OpenAI-shaped tool schema and gets back tool calls in
one shape regardless of who served the request; the mapping into each vendor's
wire format lives in that vendor's module and nowhere else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

Role = Literal["system", "user", "assistant", "tool"]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class ProviderError(RuntimeError):
    """A provider could not serve the request.

    Carries enough structure for the router to decide whether falling back to
    another provider is worthwhile, without any of it reaching the client.
    """

    def __init__(self, message: str, *, provider: str = "", retryable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    """Not configured, not reachable, or the model is not pulled."""


class ProviderTimeout(ProviderError):
    """The provider did not answer within the configured budget."""


class ProviderRateLimited(ProviderError):
    """The provider rejected the request for rate-limit or quota reasons."""


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials. Not retryable."""

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=False)


# --------------------------------------------------------------------------
# Wire types
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    #: Populated on assistant turns that requested tools, and on the tool
    #: result turns that answer them.
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class ToolSpec:
    """A tool exposed to the model, described in OpenAI JSON-schema form."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class ToolCall:
    """A model's request to invoke a tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    """Normalised generation result."""

    content: str
    model: str
    provider: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------
class LLMProvider(ABC):
    """Base class every provider implements."""

    #: Short id used in routing, logs, metrics and the ``force_model`` override.
    name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    @property
    def model_id(self) -> str:
        """Fully qualified ``provider/model`` identifier."""
        return f"{self.name}/{self.model}"

    @abstractmethod
    def is_configured(self) -> bool:
        """True when credentials/endpoints are present.

        Checked without I/O so routing can skip an unconfigured provider
        instead of paying a failed round trip for every query.
        """

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run one chat completion."""

    def cost_estimate(self, prompt_tokens: int, completion_tokens: int) -> float:
        """USD estimate for one call.

        Zero unless the deployment configures rates for this exact
        ``provider/model``: no prices are hardcoded, so an unconfigured
        provider honestly reports 0.0 rather than a fabricated figure.
        """
        from agent.config import get_settings

        settings = get_settings()
        input_rate = settings.COST_INPUT_PER_MTOK.get(self.model_id, 0.0)
        output_rate = settings.COST_OUTPUT_PER_MTOK.get(self.model_id, 0.0)
        if not (input_rate or output_rate):
            return 0.0
        return round(
            (prompt_tokens / 1_000_000) * input_rate
            + (completion_tokens / 1_000_000) * output_rate,
            8,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} model={self.model!r}>"


def coerce_tool_arguments(raw: Any) -> dict[str, Any]:
    """Normalise a provider's tool-call arguments into a dict.

    Providers disagree here: some send a JSON object, some a JSON-encoded
    string, and a small model's string is sometimes not valid JSON at all. The
    unparseable case is preserved under ``_raw`` so the model can be shown what
    it actually sent and correct it.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {}


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def classify_http_error(provider: str, response: httpx.Response) -> ProviderError:
    """Map an HTTP status onto the right error type.

    The response body is deliberately not echoed: provider error payloads
    routinely contain request echoes and account details, and none of that
    belongs in a log line or an API response.
    """
    status = response.status_code
    if status in (401, 403):
        return ProviderAuthError(
            f"{provider} rejected the configured credentials (HTTP {status}).",
            provider=provider,
        )
    if status == 408:
        return ProviderTimeout(f"{provider} timed out (HTTP 408).", provider=provider)
    if status == 429:
        return ProviderRateLimited(
            f"{provider} rate limited the request (HTTP 429).", provider=provider
        )
    if status >= 500:
        return ProviderError(f"{provider} returned HTTP {status}.", provider=provider)
    return ProviderError(
        f"{provider} rejected the request (HTTP {status}).",
        provider=provider,
        # 4xx other than auth/rate-limit is usually a malformed request, so
        # repeating it against another provider is pointless.
        retryable=status >= 500,
    )


def build_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)))
