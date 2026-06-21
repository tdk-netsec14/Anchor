"""``GET /metrics`` - aggregate counters for the running process.

In-memory and per-process by design: this is a single-machine deployment and the
brief asks for real numbers over a demo, not a time-series database. Swapping
this for a Prometheus client means reimplementing :class:`MetricsRegistry` and
nothing else - see the README's future-work section.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agent.auth import CurrentUserDep
from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.observability.metrics import registry
from agent.routing.router import get_router

router = APIRouter(tags=["observability"])
log = get_logger(__name__)


def _provider_status() -> dict[str, Any]:
    """Which providers are configured. Never calls out to the network."""
    router_instance = get_router()
    configured = router_instance.configured_providers()
    return {
        name: {
            "configured": True,
            "model": provider.model,
            "model_id": provider.model_id,
        }
        for name, provider in configured.items()
    }


@router.get(
    "/metrics",
    summary="Aggregate request, model and guardrail counters",
    description=(
        "Returns counters accumulated by this process since start-up. Requires "
        "a bearer token. Values are real measurements of served traffic, not "
        "estimates."
    ),
)
async def metrics(user: CurrentUserDep) -> dict[str, Any]:
    snapshot = registry.snapshot()
    settings = get_settings()

    snapshot["service"] = {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "embedding_model": settings.EMBEDDING_MODEL,
        "collection": settings.CHROMA_COLLECTION,
        "retriever_top_k": settings.RETRIEVER_TOP_K,
    }
    snapshot["providers"] = _provider_status()
    snapshot["requested_by"] = {"user_id": user.user_id, "role": user.role}

    log.info("metrics.read", context={"user_id": user.user_id, "role": user.role})
    return snapshot
