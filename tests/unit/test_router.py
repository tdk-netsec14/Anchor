"""Model routing: classification, explainability, force_model and fallback."""

from __future__ import annotations

import pytest

from agent.routing.providers.base import (
    Message,
    ProviderAuthError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from agent.routing.router import AllProvidersFailed, ModelRouter, RoutingDecision
from tests.fakes import ScriptedProvider, text_response


def build_router(**providers: ScriptedProvider) -> ModelRouter:
    """A router wired to ScriptedProviders instead of vendor SDKs.

    Starts from an *empty* provider registry so the real Ollama/OpenAI/... are
    never picked up as fallbacks behind the test's back.
    """
    router = ModelRouter(provider_classes={})
    for name, provider in providers.items():
        router.register(name, provider)
    return router


def decision_for(name: str) -> RoutingDecision:
    return RoutingDecision(provider=name, model="", reason="test", forced=True)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "expected_tier"),
    [
        ("How many days of annual leave do I get?", "simple"),
        ("What is the response time for a P1 incident?", "simple"),
        ("Calculate 15 percent of 2400", "medium"),
        ("How much does 3 tickets cost at 45 each?", "medium"),
        ("Create a ticket for my broken laptop", "medium"),
        ("Compare the VPN and MFA policies and explain the differences", "complex"),
        ("Troubleshoot why my VPN keeps dropping", "complex"),
        ("Summarize the expense policy step by step", "complex"),
        ("word " * 80, "complex"),
        ("a" * 500, "complex"),
    ],
)
def test_queries_classify_into_the_expected_tier(query: str, expected_tier: str) -> None:
    tier, reason = ModelRouter().classify(query)
    assert tier == expected_tier
    assert reason  # every decision is explainable


def test_every_decision_carries_a_reason() -> None:
    router = build_router(ollama=ScriptedProvider())
    for query in ["short question?", "Calculate 2+2", "Compare and analyse the policies"]:
        assert router.route(query).reason


# --------------------------------------------------------------------------
# Provider preference
# --------------------------------------------------------------------------
def test_simple_queries_prefer_the_cheapest_configured_provider() -> None:
    router = build_router(ollama=ScriptedProvider(), groq=ScriptedProvider(), openai=ScriptedProvider())
    assert router.route("What is my password policy?").provider == "ollama"


def test_tool_queries_prefer_a_tool_capable_cloud_provider() -> None:
    router = build_router(ollama=ScriptedProvider(), groq=ScriptedProvider())
    assert router.route("Calculate 12 * 12").provider == "groq"


def test_complex_queries_prefer_a_frontier_provider() -> None:
    router = build_router(
        ollama=ScriptedProvider(), groq=ScriptedProvider(),
        openai=ScriptedProvider(), gemini=ScriptedProvider(),
    )
    assert router.route("Compare and analyse the two policies in depth").provider == "openai"


def test_unconfigured_providers_are_skipped() -> None:
    router = build_router(
        ollama=ScriptedProvider(), groq=ScriptedProvider(configured=False)
    )
    assert router.route("Calculate 1+1").provider == "ollama"


# --------------------------------------------------------------------------
# force_model
# --------------------------------------------------------------------------
def test_force_model_bypasses_routing() -> None:
    router = build_router(ollama=ScriptedProvider(), groq=ScriptedProvider(model="groq-70b"))
    forced = router.route("Calculate 2+2", force_model="groq/groq-70b")
    assert (forced.provider, forced.model, forced.forced) == ("groq", "groq-70b", True)
    assert "forced" in forced.reason


def test_force_model_without_a_model_uses_the_provider_default() -> None:
    router = build_router(ollama=ScriptedProvider(model="llama-test"))
    assert router.route("hi", force_model="ollama").model == "llama-test"


def test_unknown_forced_provider_is_rejected_clearly() -> None:
    router = build_router(ollama=ScriptedProvider())
    with pytest.raises(ProviderError, match="Unknown provider"):
        router.route("hi", force_model="skynet/hal9000")


# --------------------------------------------------------------------------
# Retry and fallback
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hard_primary_failure_retries_then_falls_back() -> None:
    primary = ScriptedProvider(fail_times=99, error=ProviderTimeout("timeout"))
    backup = ScriptedProvider(script=[text_response("answered by backup", model="backup-model")])
    router = build_router(primary=primary, backup=backup)

    result = await router.execute(
        [Message(role="user", content="hi")], decision_for("primary")
    )

    assert result.response.content == "answered by backup"
    assert result.decision.provider == "backup"
    assert result.fallbacks[0]["from"] == "primary"
    assert "fell back" in result.decision.reason
    # The primary was tried once plus one retry before moving on.
    assert len(primary.calls) == 2


@pytest.mark.asyncio
async def test_retry_succeeds_without_falling_back() -> None:
    provider = ScriptedProvider(fail_times=1, error=ProviderTimeout("timeout"))
    provider.script = [text_response("recovered")]
    router = build_router(only=provider)

    result = await router.execute([Message(role="user", content="hi")], decision_for("only"))
    assert result.response.content == "recovered"
    assert result.fallbacks == []


@pytest.mark.asyncio
async def test_fallback_walks_the_chain_in_order() -> None:
    first = ScriptedProvider(fail_times=9, error=ProviderUnavailable("down"), model="a")
    second = ScriptedProvider(fail_times=9, error=ProviderUnavailable("down"), model="b")
    third = ScriptedProvider(script=[text_response("third", model="c")], model="c")
    router = build_router(a=first, b=second, c=third)
    router.fallback_providers = lambda d: [second, third]  # type: ignore[method-assign]

    result = await router.execute([Message(role="user", content="hi")], decision_for("a"))
    assert result.response.content == "third"
    assert [f["to"] for f in result.fallbacks] == ["b", "c"]


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried() -> None:
    provider = ScriptedProvider(fail_times=5, error=ProviderAuthError("bad key"))
    router = build_router(only=provider)

    with pytest.raises(AllProvidersFailed):
        await router.execute([Message(role="user", content="hi")], decision_for("only"))

    assert len(provider.calls) == 1, "bad credentials must not be retried"


@pytest.mark.asyncio
async def test_all_providers_failing_raises_with_a_summary() -> None:
    provider = ScriptedProvider(fail_times=99, error=ProviderUnavailable("all down"))
    router = build_router(only=provider)

    with pytest.raises(AllProvidersFailed) as exc:
        await router.execute([Message(role="user", content="hi")], decision_for("only"))

    assert exc.value.attempts
    assert "all down" in str(exc.value)


def test_fallback_excludes_the_primary_provider() -> None:
    router = build_router(ollama=ScriptedProvider(), groq=ScriptedProvider())
    decision = router.route("hi")
    assert decision.provider not in [p.name for p in router.fallback_providers(decision)]


@pytest.mark.asyncio
async def test_fallback_log_carries_the_actual_failure_reason() -> None:
    """The reason recorded must be the failure that caused the fallback."""
    from agent.routing.providers.base import Message

    primary = ScriptedProvider(fail_times=99, error=ProviderAuthError("bad api key"))
    backup = ScriptedProvider(script=[text_response("ok")])
    router = build_router(primary=primary, backup=backup)

    result = await router.execute([Message(role="user", content="hi")], decision_for("primary"))

    assert result.response.content == "ok"
    assert "bad api key" in result.fallbacks[0]["reason"]
    assert result.fallbacks[0]["from"] == "primary"
    assert result.fallbacks[0]["to"] == "backup"
