"""OpenAI provider, and the shared OpenAI-compatible implementation.

Most hosted inference vendors (OpenAI, Groq, Together, Fireworks, vLLM,
Ollama's own compat endpoint) expose the same ``/chat/completions`` contract.
Implementing it once here and subclassing keeps Groq to a dozen lines and means
a fix to tool-call parsing lands everywhere at the same time.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.routing.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
    build_client,
    classify_http_error,
    coerce_tool_arguments,
)

log = get_logger(__name__)



class OpenAICompatibleProvider(LLMProvider):
    """Shared implementation for any ``/chat/completions`` vendor."""

    name = "openai-compatible"
    #: Name of the config field holding this vendor's API key.
    api_key_setting: str = ""
    #: Name of the config field holding this vendor's base URL.
    base_url_setting: str = ""
    default_model_setting: str = ""
    #: Whether the key must be present in ``Authorization: Bearer``.
    uses_bearer_auth = True

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model or getattr(settings, self.default_model_setting))
        self.base_url = str(getattr(settings, self.base_url_setting)).rstrip("/")
        self.api_key = getattr(settings, self.api_key_setting, None)

    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        if not self.uses_bearer_auth:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _to_wire(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {"role": message.role, "content": message.content or None}
            if message.role == "assistant" and message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in message.tool_calls
                ]
            elif message.role == "tool":
                entry = {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content,
                }
            if message.name:
                entry["name"] = message.name
            wire.append(entry)
        return wire

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not self.is_configured():
            raise ProviderUnavailable(
                f"{self.name} is not configured (set {self.api_key_setting}).",
                provider=self.name,
            )

        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages),
            "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
            "max_tokens": settings.LLM_MAX_TOKENS if max_tokens is None else max_tokens,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            payload["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            async with build_client(settings.LLM_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{self.name} did not respond within {settings.LLM_TIMEOUT_SECONDS}s.",
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Could not reach {self.name} at {self.base_url}.", provider=self.name
            ) from exc

        if response.status_code >= 400:
            raise classify_http_error(self.name, response)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned a non-JSON response.", provider=self.name) from exc

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.name} returned no completion choices.", provider=self.name)

        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        tool_calls = [
            ToolCall(
                id=call.get("id") or f"call_{index}",
                name=(call.get("function") or {}).get("name", ""),
                arguments=coerce_tool_arguments((call.get("function") or {}).get("arguments")),
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        ]

        return LLMResponse(
            content=message.get("content") or "",
            model=data.get("model") or self.model,
            provider=self.name,
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=choices[0].get("finish_reason") or "stop",
            raw={"system_fingerprint": data.get("system_fingerprint")},
        )



class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    api_key_setting = "OPENAI_API_KEY"
    base_url_setting = "OPENAI_BASE_URL"
    default_model_setting = "OPENAI_DEFAULT_MODEL"

