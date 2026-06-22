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
    vector_store, ocr = await asyncio.gather(
        run_in_threadpool(get_vector_store().health),
        run_in_threadpool(ocr_available),
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
            "ocr": "available" if ocr else "unavailable",
            "queries_served": snapshot["total_queries"],
        },
    )
