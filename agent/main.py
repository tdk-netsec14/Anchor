"""Anchor Agent API.

Composition root: builds the FastAPI application, installs cross-cutting
middleware (request ids, access logging, metrics) and mounts the routers.
Business logic lives in the sub-packages; this module only wires it together.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agent.config import get_settings
from agent.observability.logger import (
    configure_logging,
    get_logger,
    new_request_id,
    request_id_var,
)
from agent.observability.metrics import registry
from agent.schemas.common import ErrorResponse

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def validate_production_config() -> None:
    """Refuse to start a production process that is not actually configured.

    Every check here corresponds to something that would otherwise fail
    quietly: no database means nobody can register and the development token
    endpoint would be the only way in; local storage means uploaded documents
    vanish on the next deploy; a weak pepper or a disabled pepper check means
    stolen hashes are far cheaper to attack.

    Raising at startup is deliberate. A process that boots and then behaves
    insecurely is worse than one that refuses to boot, because the deploy looks
    successful.
    """
    settings = get_settings()
    if settings.ENVIRONMENT != "prod":
        return

    problems: list[str] = []

    if not settings.DATABASE_URL.strip():
        problems.append(
            "DATABASE_URL is not set. Production Anchor is multi-tenant and needs a database; "
            "run the migrations with `alembic upgrade head`."
        )
    elif settings.DATABASE_URL.startswith("sqlite"):
        problems.append(
            "DATABASE_URL points at SQLite. Use a managed PostgreSQL instance in production."
        )

    if settings.STORAGE_BACKEND != "s3":
        problems.append(
            f"STORAGE_BACKEND is '{settings.STORAGE_BACKEND}'. A container filesystem is wiped "
            "on every deploy, so uploaded documents must live in object storage. Set it to 's3'."
        )
    elif not settings.S3_BUCKET.strip():
        problems.append("STORAGE_BACKEND is 's3' but S3_BUCKET is not set.")

    if not settings.CREDENTIAL_PEPPER.strip():
        problems.append(
            "CREDENTIAL_PEPPER is not set. Passwords are then protected by Argon2 alone, with no "
            "server-side secret an attacker cannot reach."
        )

    if len(settings.JWT_SECRET) < 32:
        problems.append("JWT_SECRET must be at least 32 characters in production.")

    if settings.CORS_ALLOWED_ORIGINS.strip() == "*":
        problems.append(
            "CORS_ALLOWED_ORIGINS is '*'. Name the exact origins that may call the API."
        )

    if not problems:
        return

    raise RuntimeError(
        "Anchor cannot start in production:\n"
        + "\n".join(f"  - {problem}" for problem in problems)
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    log.info(
        "anchor.startup",
        context={"service": settings.APP_NAME, "env": settings.ENVIRONMENT},
    )
    validate_production_config()

    # Open the vector store eagerly: it surfaces a misconfigured persist
    # directory or collection name at boot instead of on a user's first query.
    # Deliberately non-fatal - the process is still useful (and /health still
    # answers) if storage is temporarily unavailable.
    try:
        from ingestion.vector_store import get_vector_store

        get_vector_store().initialize()
        log.info("anchor.vector_store_ready")
    except Exception as exc:
        log.error("anchor.vector_store_unavailable", context={"reason": type(exc).__name__})
    yield
    log.info("anchor.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)

    app = FastAPI(
        title="Anchor",
        version=settings.APP_VERSION,
        summary="Internal AI support and research agent (RAG + tool calling).",
        description=(
            "Anchor answers internal support questions from a private document "
            "knowledge base, routes each question to an appropriate LLM "
            "provider, and can call internal tools on the user's behalf.\n\n"
            "All routes except `GET /health` require a bearer token obtained "
            "from `POST /auth/token`. Ingestion additionally requires the "
            "`admin` role."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Attach a request id, emit one access log line, and time the call."""
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        registry.record_request(request.url.path)

        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Never leak an internal traceback to the caller: substitute a
                # safe response and keep the detail in the log, where the
                # request id makes it findable.
                log.error(
                    "request.unhandled_exception",
                    context={
                        "endpoint": request.url.path,
                        "method": request.method,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                    exc_info=True,
                )
                registry.record_error("unhandled_exception")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": "internal_error",
                        "message": "An unexpected error occurred. Please try again.",
                        "request_id": request_id,
                        "guardrail_flags": [],
                    },
                )
        finally:
            # Exactly one reset, on every path. Resetting a ContextVar token
            # twice raises, which would replace the safe 500 above with an
            # opaque error of its own.
            request_id_var.reset(token)

        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 500:
            registry.record_error(f"http_{response.status_code}")

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.2f}"
        log.info(
            "request.completed",
            context={
                "endpoint": request.url.path,
                "method": request.method,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
            },
        )
        return response

    from agent.routers import (
        analytics,
        api_keys,
        auth,
        conversations,
        documents,
        health,
        ingest,
        metrics,
        query,
        workspaces,
    )

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    app.include_router(conversations.router)
    app.include_router(workspaces.router)
    app.include_router(api_keys.router)
    app.include_router(analytics.router)
    app.include_router(metrics.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Render every failure as the same ErrorResponse shape.

        Routers raise with a structured ``detail``; this flattens that into a
        stable top-level body and stamps the request id so a user-reported
        failure can be found in the logs. Building it through the Pydantic
        model also documents the error shape in the OpenAPI schema.
        """
        detail = exc.detail
        payload = detail if isinstance(detail, dict) else {}
        body = ErrorResponse(
            error=payload.get("error", "http_error"),
            message=payload.get(
                "message", str(detail) if detail else "The request could not be completed."
            ),
            request_id=getattr(request.state, "request_id", None),
            guardrail_flags=payload.get("guardrail_flags", []),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(),
            headers=exc.headers or {},
        )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """Send a bare visit to the demo page; the API itself lives under /docs."""
        return RedirectResponse(url="/ui", status_code=307)

    # The demo page is served same-origin, so it needs no CORS. The middleware
    # exists only so a UI can be developed on a different port, and is disabled
    # by setting CORS_ALLOWED_ORIGINS to an empty string.
    if settings.CORS_ALLOWED_ORIGINS.strip():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-Response-Time-ms"],
        )

    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


app = create_app()
