"""Deterministic, explainable model routing with provider fallback.

The router answers two questions:

1. *Which model should try this query?* - by cheap deterministic heuristics
   (length, intent keywords, whether a tool is likely needed). No classifier
   model, no extra latency, and every decision comes with a human-readable
   reason that ends up in the logs and the response.
2. *What happens when it fails?* - retry once, then walk the configured
   fallback chain until a provider answers. A caller never sees a provider
   exception; the worst case is a clean 503.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass

from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.observability.metrics import registry
from agent.routing.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderAuthError,
    ProviderError,
    ToolSpec,
)
from agent.routing.providers.gemini_provider import GeminiProvider
from agent.routing.providers.groq_provider import GroqProvider
from agent.routing.providers.ollama_provider import OllamaProvider
from agent.routing.providers.openai_provider import OpenAIProvider

log = get_logger(__name__)

PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "groq": GroqProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

#: Preferred provider order per complexity tier, cheapest first. A provider
#: missing from this list (or unconfigured) is skipped.
TIER_PREFERENCE: dict[str, list[str]] = {
    "simple": ["ollama", "groq", "openai", "gemini"],
    "medium": ["groq", "ollama", "openai", "gemini"],
    "complex": ["openai", "gemini", "groq", "ollama"],
}

#: Words that signal a tool is likely needed.
TOOL_INTENT = re.compile(
    r"\b(calculat\w*|compute|how much|what is \d|total|sum of|"
    r"create (a )?ticket|raise (a )?ticket|escalat\w+|open (a )?ticket|"
    r"search (the )?(docs|documentation|kb|knowledge)|look up|find (me )?(the )?policy)\b",
    re.IGNORECASE,
)

#: Words that signal a question needing synthesis rather than lookup.
COMPLEX_INTENT = re.compile(
    r"\b(compare|contrast|analyse|analyze|explain why|troubleshoot|diagnose|"
    r"design|evaluate|trade-?offs?|summarise|summarize|step by step|"
    r"pros and cons|what would happen if)\b",
    re.IGNORECASE,
)

#: A query at or beyond this many words, or this many characters, is complex.
#: Character length matters because a wall of run-together text is one enormous
#: "word" and would otherwise slip past a word-count check.
LONG_QUERY_WORDS = 60
LONG_QUERY_CHARS = 400


class AllProvidersFailed(RuntimeError):
    """Every provider in the chain failed. Surfaces to the caller as a 503."""

    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        self.attempts = attempts
        detail = ", ".join(f"{name}: {reason}" for name, reason in attempts)
        super().__init__(f"No LLM provider could serve the request ({detail}).")


@dataclass(slots=True)
class RoutingDecision:
    provider: str
    model: str
    reason: str
    tier: str = "simple"
    forced: bool = False

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(slots=True)
class ProviderResult:
    response: LLMResponse
    decision: RoutingDecision
    #: Providers that were tried and failed before this one succeeded.
    fallbacks: list[dict[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fallbacks is None:
            self.fallbacks = []


class ModelRouter:
    """Chooses a provider and executes against it, with fallback."""

    def __init__(
        self,
        provider_classes: dict[str, type[LLMProvider]] | None = None,
        tier_preference: dict[str, list[str]] | None = None,
    ) -> None:
        # Both maps are injectable so tests (and alternative deployments) can
        # substitute providers without monkeypatching module globals. `is
        # None` rather than `or`, so an explicitly empty map stays empty.
        self.provider_classes = PROVIDER_CLASSES if provider_classes is None else provider_classes
        self.tier_preference = TIER_PREFERENCE if tier_preference is None else tier_preference
        self._providers: dict[str, LLMProvider] = {}

    # -- provider access ---------------------------------------------------
    def get_provider(self, name: str, model: str | None = None) -> LLMProvider:
        """Return (and memoise) a provider instance by id."""
        key = f"{name}|{model or ''}"
        if key in self._providers:
            return self._providers[key]
        cls = self.provider_classes.get(name)
        if cls is None:
            raise ProviderError(
                f"Unknown provider '{name}'. Available: {', '.join(sorted(self.provider_classes))}.",
                provider=name,
                retryable=False,
            )
        provider = cls(model)
        self._providers[key] = provider
        return provider

    def register(self, name: str, provider: LLMProvider) -> None:
        """Register a pre-built provider instance under ``name``.

        Used by tests (which inject scripted providers) and by deployments that
        construct providers programmatically rather than from config. The
        registration key becomes the provider's identity - it is what appears
        in routing reasons, fallback records and metrics - so it is bound onto
        the instance, overriding the class-level default.
        """
        provider.name = name
        self.provider_classes[name] = type(provider)
        self._providers[f"{name}|"] = provider
        self._providers[f"{name}|{provider.model}"] = provider

    def configured_providers(self) -> dict[str, LLMProvider]:
        """Every provider that has what it needs to run, keyed by id."""
        available: dict[str, LLMProvider] = {}
        for name in self.provider_classes:
            try:
                provider = self.get_provider(name)
            except ProviderError:
                continue
            if provider.is_configured():
                available[name] = provider
        return available

    # -- routing -----------------------------------------------------------
    def classify(self, query: str) -> tuple[str, str]:
        """Return ``(tier, reason)`` for a query.

        Intentionally simple and inspectable: a support assistant whose model
        choice you cannot explain is a support assistant nobody can tune.
        """
        words = query.split()
        if TOOL_INTENT.search(query):
            return "medium", f"tool-intent keyword detected in a {len(words)}-word query"
        if COMPLEX_INTENT.search(query):
            return "complex", f"analysis/synthesis keyword detected in a {len(words)}-word query"
        if len(words) >= LONG_QUERY_WORDS or len(query) >= LONG_QUERY_CHARS:
            return "complex", (
                f"long query ({len(words)} words / {len(query)} chars) "
                f">= {LONG_QUERY_WORDS} words or {LONG_QUERY_CHARS} chars"
            )
        return "simple", f"short factual query ({len(words)} words, no tool or analysis signal)"

    def route(self, query: str, *, force_model: str | None = None) -> RoutingDecision:
        """Select a provider, honouring an explicit override when present."""
        if force_model:
            provider_name, _, model = force_model.partition("/")
            provider_name = provider_name.strip()
            if provider_name not in self.provider_classes:
                raise ProviderError(
                    f"Unknown provider in force_model '{force_model}'. "
                    f"Available: {sorted(self.provider_classes)}.",
                    provider=provider_name,
                    retryable=False,
                )
            model = model or self.get_provider(provider_name).model
            return RoutingDecision(
                provider=provider_name,
                model=model,
                reason=f"forced by request (force_model={force_model})",
                tier="forced",
                forced=True,
            )

        settings = get_settings()
        tier, reason = self.classify(query)
        available = self.configured_providers()

        for candidate in self.tier_preference[tier]:
            if candidate in available:
                return RoutingDecision(
                    provider=candidate,
                    model=available[candidate].model,
                    reason=f"{tier} query -> {reason}; cheapest configured provider is '{candidate}'",
                    tier=tier,
                )

        if available:
            # Something is configured but none of it is in this tier's
            # preference list (an unusual configuration, or a custom provider).
            # A working provider beats a 503.
            name = sorted(available)[0]
            return RoutingDecision(
                provider=name,
                model=available[name].model,
                reason=f"{tier} query -> {reason}; no tier match, using configured '{name}'",
                tier=tier,
            )

        # Nothing is configured at all - name the default so the eventual
        # failure is a clear provider error rather than an empty routing.
        default_provider, _, default_model = settings.ROUTER_DEFAULT_MODEL.partition("/")
        if default_provider not in self.provider_classes:
            default_provider = sorted(self.provider_classes)[0]
        return RoutingDecision(
            provider=default_provider,
            model=default_model or self.get_provider(default_provider).model,
            reason=f"{tier} query -> {reason}; no provider is configured",
            tier=tier,
        )

    def fallback_providers(self, decision: RoutingDecision) -> list[LLMProvider]:
        """Providers to try after the primary one, in order, skipping the primary."""
        settings = get_settings()
        available = self.configured_providers()
        ordered: list[LLMProvider] = []
        used = {decision.provider}

        for name in settings.fallback_chain:
            if name in available and name not in used:
                ordered.append(available[name])
                used.add(name)

        # Anything else that is configured is still a valid last resort - a
        # slower answer beats a 503. Ordered by tier preference first, then
        # alphabetically so the chain is deterministic.
        extras = [n for n in self.tier_preference["complex"] if n in available and n not in used]
        extras += sorted(n for n in available if n not in used and n not in extras)
        ordered.extend(available[n] for n in extras)
        return ordered

    # -- execution ---------------------------------------------------------
    async def execute(
        self,
        messages: Sequence[Message],
        decision: RoutingDecision,
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> ProviderResult:
        """Run the completion, retrying then falling back until something answers."""
        settings = get_settings()
        attempts: list[tuple[str, str]] = []
        fallbacks: list[dict[str, str]] = []

        candidates: list[LLMProvider] = [self.get_provider(decision.provider, decision.model)]
        candidates.extend(self.fallback_providers(decision))

        previous = decision.provider
        previous_reason = "primary provider failed"

        for index, provider in enumerate(candidates):
            last_reason = "unknown"
            for attempt in range(settings.PROVIDER_MAX_RETRIES + 1):
                try:
                    response = await provider.generate(messages, tools=tools)
                except ProviderAuthError as exc:
                    # Bad credentials will not fix themselves on retry.
                    last_reason = str(exc)
                    attempts.append((provider.name, last_reason))
                    log.error(
                        "provider.auth_failed",
                        context={
                            "provider": provider.name,
                            "model": provider.model,
                            "attempt": attempt + 1,
                        },
                    )
                    break
                except ProviderError as exc:
                    last_reason = str(exc)
                    attempts.append((provider.name, last_reason))
                    if exc.retryable and attempt < settings.PROVIDER_MAX_RETRIES:
                        log.warning(
                            "provider.retry",
                            context={
                                "provider": provider.name,
                                "attempt": attempt + 1,
                                "reason": last_reason,
                            },
                        )
                        # Small backoff so a rate-limited provider is not hammered.
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    log.warning(
                        "provider.failed",
                        context={
                            "provider": provider.name,
                            "model": provider.model,
                            "error_type": type(exc).__name__,
                            "reason": last_reason,
                        },
                    )
                    break
                else:
                    if previous != provider.name:
                        # `previous_reason` is the failure that got us here;
                        # `last_reason` belongs to this (successful) provider.
                        registry.record_fallback(previous, provider.name, previous_reason[:80])
                        log.warning(
                            "provider.fallback",
                            context={
                                "from": previous,
                                "to": provider.name,
                                "reason": previous_reason,
                            },
                        )
                    return ProviderResult(
                        response=response,
                        decision=RoutingDecision(
                            provider=provider.name,
                            model=response.model,
                            reason=(
                                decision.reason
                                if provider.name == decision.provider
                                else f"fell back to '{provider.name}' after "
                                f"{decision.provider} failed"
                            ),
                            tier=decision.tier,
                            forced=decision.forced,
                        ),
                        fallbacks=fallbacks,
                    )

            nxt = candidates[index + 1].name if index + 1 < len(candidates) else "none"
            fallbacks.append({"from": previous, "to": nxt, "reason": last_reason})
            previous = provider.name
            previous_reason = last_reason

        log.error(
            "provider.all_failed",
            context={"attempts": [name for name, _ in attempts], "providers": len(candidates)},
        )
        registry.record_error("all_providers_failed")
        raise AllProvidersFailed(attempts)


_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
