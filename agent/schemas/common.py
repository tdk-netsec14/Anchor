"""Response envelopes shared by every router."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Uniform error body.

    Internal exceptions never reach the client: routers translate failures into
    this shape so a caller always sees a stable, non-leaking contract.
    """

    error: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable, safe-to-display explanation.")
    request_id: str | None = Field(
        default=None, description="Correlates the failure with server logs."
    )
    guardrail_flags: list[str] = Field(
        default_factory=list, description="Guardrails that shaped this response."
    )


class HealthResponse(BaseModel):
    """Liveness payload for ``GET /health``."""

    status: str
    service: str
    version: str
    environment: str
    uptime_seconds: float
    checks: dict[str, Any] = Field(default_factory=dict)
