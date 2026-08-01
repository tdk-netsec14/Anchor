"""Liveness endpoint.

``GET /health`` is intentionally the only unauthenticated route: an orchestrator
must be able to probe the container without holding a token.

It never returns a non-200 status. A degraded dependency (Ollama down, Chroma
unreachable) is reported inside ``checks`` while the process itself is still
alive and able to serve fallback traffic — failing the probe would just restart
a container that is working as designed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from agent.config import get_settings
from agent.observability.metrics import registry
from agent.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and dependency check",
)
async def health() -> HealthResponse:
    settings = get_settings()
    snapshot = registry.snapshot()

    from ingestion.ocr import ocr_available
    from ingestion.vector_store import get_vector_store

    # Both probes block: the store hits disk and the OCR check spawns the
    # tesseract binary. An orchestrator polls this route on a timer, so running
    # them on the event loop would stall every other request each time.
    vector_store, ocr, database = await asyncio.gather(
        run_in_threadpool(get_vector_store().health),
        run_in_threadpool(ocr_available),
        run_in_threadpool(_database_health),
    )

    return HealthResponse(
        status="ok",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        uptime_seconds=snapshot["uptime_seconds"],
        checks={
            "config": "ok",
            "vector_store": vector_store,
            "database": database,
            "storage": {"backend": settings.STORAGE_BACKEND, "status": "configured"},
            "ocr": "available" if ocr else "unavailable",
            "queries_served": snapshot["total_queries"],
        },
    )


def _database_health() -> dict[str, Any]:
    """Non-throwing database probe.

    ``not_configured`` is reported rather than treated as a fault: a
    development deployment with no database is a supported mode, and
    ``ENVIRONMENT=prod`` cannot start without one.
    """
    from agent.db.session import database_configured

    if not database_configured():
        return {"status": "not_configured"}
    try:
        from sqlalchemy import text

        from agent.db.base import get_engine

        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        # The exception type, not its message: a connection error can name the
        # host and the credentials it failed on.
        return {"status": "unavailable", "error": type(exc).__name__}


@router.get(
    "/settings",
    summary="Safe runtime configuration info",
    description="Returns safe configuration options without exposing credentials or keys.",
)
async def settings_info() -> dict[str, Any]:
    settings = get_settings()
    from agent.routing.router import get_router
    router_instance = get_router()
    configured_providers = list(router_instance.configured_providers().keys())
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "default_model": settings.ROUTER_DEFAULT_MODEL,
        "fallback_chain": settings.fallback_chain,
        "configured_providers": configured_providers,
        "embedding_model": settings.EMBEDDING_MODEL,
        "chroma_collection": settings.CHROMA_COLLECTION,
        "retriever_top_k": settings.RETRIEVER_TOP_K,
        "chunk_size_tokens": settings.CHUNK_SIZE_TOKENS,
        "chunk_overlap_tokens": settings.CHUNK_OVERLAP_TOKENS,
        "query_max_chars": settings.QUERY_MAX_CHARS,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "ocr_enabled": settings.OCR_ENABLED,
        "ocr_language": settings.OCR_LANGUAGE,
        "llm_temperature": settings.LLM_TEMPERATURE,
        "llm_max_tokens": settings.LLM_MAX_TOKENS,
    }

