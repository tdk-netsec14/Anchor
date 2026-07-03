"""Ollama provider — the local, credential-free default.

Ollama is the only provider that works with an empty configuration, which makes
it Anchor's default target: the system is fully functional on a laptop with no
accounts and no spend, and the cloud providers are pure upgrades.
"""

from __future__ import annotations

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


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model or settings.OLLAMA_DEFAULT_MODEL)
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    def is_configured(self) -> bool:
        # No credentials required; only the endpoint has to look sane.
        return bool(self.base_url) and self.base_url.startswith(("http://", "https://"))

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/chat"

    def _to_wire(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "function": {
                            "name": call.name,
                            "arguments": coerce_tool_arguments(call.arguments),
                        }
                    }
                    for call in message.tool_calls
                ]
            if message.tool_call_id:
                entry["tool_call_id"] = message.tool_call_id
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
                f"Ollama endpoint is not usable (OLLAMA_BASE_URL={self.base_url!r}).",
                provider=self.name,
            )
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages),
            "stream": False,
            "options": {
                "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
                "num_predict": settings.LLM_MAX_TOKENS if max_tokens is None else max_tokens,
            },
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]

        started = time.perf_counter()
        try:
            async with build_client(settings.LLM_TIMEOUT_SECONDS) as client:
                response = await client.post(self.endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"Ollama did not respond within {settings.LLM_TIMEOUT_SECONDS}s.",
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Could not reach Ollama at {self.base_url}. Is `ollama serve` running?",
                provider=self.name,
            ) from exc

        if response.status_code >= 400:
            if response.status_code == 404:
                # By far the most common operational problem: the model is
                # configured but has not been pulled. Say so, and say how to
                # fix it, rather than reporting a bare 404.
                raise ProviderUnavailable(
                    f"Ollama does not have the model '{self.model}'. "
                    f"Pull it with: ollama pull {self.model}",
                    provider=self.name,
                )
            raise classify_http_error("Ollama", response)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Ollama returned a non-JSON response.", provider=self.name) from exc

        message = data.get("message") or {}
        tool_calls = [
            ToolCall(
                id=f"call_{index}",
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
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=data.get("done_reason") or "stop",
            raw={"total_duration_ns": data.get("total_duration")},
        )
