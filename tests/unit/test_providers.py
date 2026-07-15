"""Provider wire-format and error-mapping tests.

The providers build their own HTTP client, so the transport is swapped out at
the `build_client` seam rather than by monkeypatching a private. That lets each
vendor's request shape, response parsing and error mapping be asserted exactly,
with no network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent.routing.providers import base as provider_base
from agent.routing.providers.base import (
    LLMResponse,
    Message,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
    classify_http_error,
)
from agent.routing.providers.gemini_provider import GeminiProvider
from agent.routing.providers.groq_provider import GroqProvider
from agent.routing.providers.ollama_provider import OllamaProvider
from agent.routing.providers.openai_provider import OpenAIProvider

MESSAGES = [Message(role="user", content="hello")]


@pytest.fixture
def mock_transport(monkeypatch):
    """Capture outgoing requests and return a canned response."""

    def install(
        handler,
    ) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def factory(timeout: float) -> httpx.AsyncClient:
            def handle(request: httpx.Request) -> httpx.Response:
                request.read()
                seen.append(request)
                return handler(request)

            return httpx.AsyncClient(transport=httpx.MockTransport(handle))

        monkeypatch.setattr(provider_base, "build_client", factory)
        for module in (
            "agent.routing.providers.ollama_provider",
            "agent.routing.providers.openai_provider",
            "agent.routing.providers.gemini_provider",
        ):
            monkeypatch.setattr(
                __import__(module, fromlist=["build_client"]), "build_client", factory
            )
        return seen

    return install


def json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def with_key(provider):
    """Give a cloud provider a dummy key so it attempts the request.

    A provider with no key refuses to call out at all, which is correct
    behaviour - these tests are about the wire format, not the credentials.
    """
    provider.api_key = "test-key-not-a-real-key"
    return provider


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------
class TestOllama:
    @pytest.mark.asyncio
    async def test_parses_content_and_token_counts(self, mock_transport) -> None:
        seen = mock_transport(
            lambda r: json_response(
                {
                    "model": "llama3.2:3b",
                    "message": {"role": "assistant", "content": "42"},
                    "prompt_eval_count": 11,
                    "eval_count": 3,
                    "done_reason": "stop",
                }
            )
        )
        result = await OllamaProvider("llama3.2:3b").generate(MESSAGES)

        assert result.content == "42"
        assert result.prompt_tokens == 11
        assert result.completion_tokens == 3
        assert result.total_tokens == 14
        assert result.provider == "ollama"

        body = json.loads(seen[0].content)
        assert body["stream"] is False, "streaming would break the request/response contract"
        assert body["messages"][0] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_parses_tool_calls(self, mock_transport) -> None:
        mock_transport(
            lambda r: json_response(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "calculator", "arguments": {"expression": "2+2"}}}
                        ],
                    }
                }
            )
        )
        result = await OllamaProvider("llama3.2:3b").generate(
            MESSAGES, tools=[ToolSpec(name="calculator", description="d", parameters={})]
        )
        assert result.wants_tools
        assert result.tool_calls[0].name == "calculator"
        assert result.tool_calls[0].arguments == {"expression": "2+2"}

    @pytest.mark.asyncio
    async def test_missing_model_gives_an_actionable_error(self, mock_transport) -> None:
        mock_transport(lambda r: httpx.Response(404, json={"error": "model not found"}))
        with pytest.raises(ProviderUnavailable) as exc:
            await OllamaProvider("llama3.2:3b").generate(MESSAGES)
        # The most common operational mistake deserves a fix, not a bare 404.
        assert "ollama pull llama3.2:3b" in str(exc.value)

    @pytest.mark.asyncio
    async def test_timeout_maps_to_provider_timeout(self, mock_transport) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        mock_transport(timeout)
        with pytest.raises(ProviderTimeout):
            await OllamaProvider().generate(MESSAGES)

    @pytest.mark.asyncio
    async def test_connection_error_maps_to_unavailable(self, mock_transport) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        mock_transport(down)
        with pytest.raises(ProviderUnavailable) as exc:
            await OllamaProvider().generate(MESSAGES)
        assert "ollama serve" in str(exc.value)


# --------------------------------------------------------------------------
# OpenAI-compatible (OpenAI + Groq)
# --------------------------------------------------------------------------
OPENAI_BODY = {
    "model": "llama-3.3-70b-versatile",
    "choices": [
        {"message": {"content": "hello there"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 5},
}


class TestOpenAICompatible:
    @pytest.mark.asyncio
    async def test_groq_parses_response_and_sends_bearer(self, mock_transport) -> None:
        seen = mock_transport(lambda r: json_response(OPENAI_BODY))
        provider = with_key(GroqProvider("llama-3.3-70b-versatile"))
        result = await provider.generate(MESSAGES)

        assert result.content == "hello there"
        assert result.total_tokens == 25
        assert seen[0].headers["authorization"] == f"Bearer {provider.api_key}"
        assert seen[0].url.path.endswith("/chat/completions")

    @pytest.mark.asyncio
    async def test_tool_calls_with_json_string_arguments(self, mock_transport) -> None:
        mock_transport(
            lambda r: json_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "function": {
                                            "name": "search_kb",
                                            "arguments": '{"query": "leave policy"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            )
        )
        result = await with_key(GroqProvider()).generate(MESSAGES)
        call = result.tool_calls[0]
        assert call.id == "call_1"
        # Arguments arrive as a JSON string and must be parsed.
        assert call.arguments == {"query": "leave policy"}

    @pytest.mark.asyncio
    async def test_malformed_arguments_do_not_crash(self, mock_transport) -> None:
        mock_transport(
            lambda r: json_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {"id": "c", "function": {"name": "calculator", "arguments": "{oops"}}
                                ],
                            }
                        }
                    ]
                }
            )
        )
        result = await with_key(GroqProvider()).generate(MESSAGES)
        # Preserved verbatim so the model can be told what it did wrong.
        assert result.tool_calls[0].arguments == {"_raw": "{oops"}

    @pytest.mark.asyncio
    async def test_unconfigured_provider_reports_itself(self, mock_transport) -> None:
        provider = OpenAIProvider()
        provider.api_key = None
        assert provider.is_configured() is False
        with pytest.raises(ProviderUnavailable) as exc:
            await provider.generate(MESSAGES)
        assert "OPENAI_API_KEY" in str(exc.value)

    @pytest.mark.asyncio
    async def test_empty_choices_is_an_error_not_a_crash(self, mock_transport) -> None:
        mock_transport(lambda r: json_response({"choices": []}))
        with pytest.raises(ProviderError, match="no completion choices"):
            await with_key(GroqProvider()).generate(MESSAGES)

    def test_cost_estimate_is_zero_without_configured_rates(self) -> None:
        assert GroqProvider().cost_estimate(1000, 1000) == 0.0


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
class TestGemini:
    @pytest.mark.asyncio
    async def test_translates_system_prompt_and_parses_response(self, mock_transport) -> None:
        seen = mock_transport(
            lambda r: json_response(
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "grounded answer"}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4},
                }
            )
        )
        provider = with_key(GeminiProvider("gemini-1.5-flash"))
        result = await provider.generate(
            [Message(role="system", content="be helpful"), *MESSAGES]
        )

        assert result.content == "grounded answer"
        assert result.total_tokens == 13
        body = json.loads(seen[0].content)
        assert body["systemInstruction"] == {"parts": [{"text": "be helpful"}]}
        assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]

    @pytest.mark.asyncio
    async def test_parses_function_calls(self, mock_transport) -> None:
        mock_transport(
            lambda r: json_response(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"functionCall": {"name": "calculator", "args": {"expression": "1+1"}}}
                                ]
                            }
                        }
                    ]
                }
            )
        )
        provider = with_key(GeminiProvider())
        result = await provider.generate(MESSAGES)
        assert result.tool_calls[0].name == "calculator"
        assert result.tool_calls[0].arguments == {"expression": "1+1"}

    @pytest.mark.asyncio
    async def test_quota_error_on_400_is_retried_not_abandoned(self, mock_transport) -> None:
        """Gemini reports quota as 400, which the generic mapper marks permanent."""
        mock_transport(
            lambda r: httpx.Response(400, json={"error": {"message": "Quota exceeded"}})
        )
        provider = GeminiProvider()
        provider.api_key = "test-key"
        with pytest.raises(ProviderRateLimited):
            await provider.generate(MESSAGES)


# --------------------------------------------------------------------------
# Shared error mapping
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (401, ProviderAuthError, False),
        (403, ProviderAuthError, False),
        (408, ProviderTimeout, True),
        (429, ProviderRateLimited, True),
        (500, ProviderError, True),
        (503, ProviderError, True),
        (400, ProviderError, False),
    ],
)
def test_http_status_maps_to_the_right_error(
    status: int, expected: type, retryable: bool
) -> None:
    error = classify_http_error("TestProvider", httpx.Response(status, json={}))
    assert isinstance(error, expected)
    assert error.retryable is retryable


def test_error_messages_never_echo_the_response_body() -> None:
    """Provider errors routinely contain request echoes and account details."""
    response = httpx.Response(
        500, json={"error": {"message": "key sk-live-SECRET invalid for acct 12345"}}
    )
    assert "SECRET" not in str(classify_http_error("Test", response))


def test_tool_spec_renders_openai_schema() -> None:
    spec = ToolSpec(name="calculator", description="d", parameters={"type": "object"})
    rendered = spec.to_openai()
    assert rendered["type"] == "function"
    assert rendered["function"]["name"] == "calculator"


def test_wire_type_defaults() -> None:
    """Defaults are part of the contract the agent loop relies on."""
    empty_call = ToolCall(id="1", name="calculator", arguments={})
    assert empty_call.arguments == {}

    response = LLMResponse(content="hi", model="m", provider="p")
    assert response.wants_tools is False
    assert response.total_tokens == 0
    assert response.finish_reason == "stop"
    assert response.tool_calls == []
