"""Groq provider.

Groq is the default *cloud* target: it is OpenAI-compatible, has a generous
free tier, and its hosted models handle tool calling well. The whole provider is
a configuration binding because the wire format is identical to OpenAI's.
"""

from __future__ import annotations

from agent.routing.providers.openai_provider import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    api_key_setting = "GROQ_API_KEY"
    base_url_setting = "GROQ_BASE_URL"
    default_model_setting = "GROQ_DEFAULT_MODEL"
