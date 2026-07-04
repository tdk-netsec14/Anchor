"""Google Gemini provider.

Gemini does *not* speak the OpenAI wire format on its native endpoint, so this
module does its own message and tool translation. The point of writing it is
that the abstraction earns its keep here: because nothing outside this file
knows Gemini's ``contents`` / ``functionCall`` shape, adding it touched no
business logic.
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
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
    build_client,
    classify_http_error,
)

log = get_logger(__name__)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        super().__init__(model or settings.GEMINI_DEFAULT_MODEL)
        self.base_url = settings.GEMINI_BASE_URL.rstrip("/")
        self.api_key = settings.GEMINI_API_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_url)

    def _split(self, messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Return (system instruction, contents)."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            if message.role == "assistant":
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    parts.append(
                        {"functionCall": {"name": call.name, "args": call.arguments}}
                    )
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif message.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": message.name or "",
                                    "response": {"result": message.content},
                                }
                            }
                        ],
                    }
                )
            else:
                contents.append({"role": "user", "parts": [{"text": message.content}]})
        return "\n\n".join(system_parts), contents

    def _tools_payload(self, tools: Sequence[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "functionDeclarations": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    }
                    for t in tools
                ]
            }
        ]

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
                "Gemini is not configured (set GEMINI_API_KEY).", provider=self.name
            )

        settings = get_settings()
        system_instruction, contents = self._split(messages)

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
                "maxOutputTokens": settings.LLM_MAX_TOKENS if max_tokens is None else max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        tool_payload = self._tools_payload(tools)
        if tool_payload:
            payload["tools"] = tool_payload

        started = time.perf_counter()
        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            async with build_client(settings.LLM_TIMEOUT_SECONDS) as client:
                response = await client.post(url, params={"key": self.api_key}, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"Gemini did not respond within {settings.LLM_TIMEOUT_SECONDS}s.",
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"Could not reach Gemini at {self.base_url}.", provider=self.name
            ) from exc

        if response.status_code >= 400:
            # Gemini reports quota exhaustion as 400, not 429, so the generic
            # classifier would mark it non-retryable and we would give up on a
            # provider that would work a moment later.
            if response.status_code == 400 and "quota" in response.text.lower():
                raise ProviderRateLimited("Gemini quota exhausted.", provider=self.name) from None
            raise classify_http_error("Gemini", response)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Gemini returned a non-JSON response.", provider=self.name) from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini returned no candidates.", provider=self.name)

        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            if "text" in part and part["text"]:
                text_parts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"] or {}
                tool_calls.append(
                    ToolCall(
                        id=f"call_{index}",
                        name=call.get("name", ""),
                        arguments=call.get("args") or {},
                    )
                )

        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            content="".join(text_parts),
            model=self.model,
            provider=self.name,
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=candidates[0].get("finishReason") or "stop",
            raw={"promptFeedback": data.get("promptFeedback")},
        )

