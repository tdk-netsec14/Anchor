"""Workspace API keys.

A key is a bearer credential for machine callers, scoped to one workspace. The
secret is 32 bytes of CSPRNG output, shown exactly once at creation and stored
only as a SHA-256 hash — there is no way to recover it afterwards, which is the
point: a database leak does not yield usable keys.

Keys carry scopes, an optional expiry, revocation, and a last-used timestamp.
Creating, revoking and using one are all audit events.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import audit, rate_limit
from agent.auth.principals import CurrentUser, require_workspace_role
from agent.db.models import ApiKey, WorkspaceRole
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger
from agent.rate_limit import principal_key
from agent.security import hash_token

router = APIRouter(prefix="/api-keys", tags=["api keys"])
log = get_logger(__name__)

_AdminDep = Annotated[CurrentUser, Depends(require_workspace_role(WorkspaceRole.ADMIN))]

#: The scopes a key may hold. A key is always bound to a workspace, so there is
#: no cross-tenant scope to grant.
ALL_SCOPES = ("query", "documents:read", "documents:write", "conversations:read", "conversations:write")
DEFAULT_SCOPES = ("query", "documents:read")
MAX_KEYS_PER_WORKSPACE = 50


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    #: Days until expiry. ``None`` means it does not expire.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    #: Enough to identify the key in a list. The rest is not recoverable.
    key_prefix: str
    scopes: list[str]
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None
    created_by: str | None = None


class ApiKeyCreatedResponse(ApiKeyResponse):
    #: Present only in this response. Shown once and never again.
    key: str


def _require_db(db: Session | None) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "database_unavailable",
                "message": "API keys need a database. Set DATABASE_URL and run the migrations.",
            },
        )
    return db


def _to_response(row: ApiKey, created_by: str | None = None) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        scopes=[s for s in row.scopes.split(",") if s],
        created_at=row.created_at.isoformat(),
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        created_by=created_by,
    )


def _mint_key() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, hash)``.

    The prefix is the first characters after ``ank_`` — short enough to be
    meaningless on its own, long enough to tell two keys apart in a list.
    """
    secret = secrets.token_urlsafe(32)
    full = f"ank_{secret}"
    prefix = full[:12]
    return full, prefix, hash_token(full)


@router.get("", response_model=list[ApiKeyResponse], summary="List API keys")
async def list_keys(user: _AdminDep, db: OptionalDbSession) -> list[ApiKeyResponse]:
    session = _require_db(db)
    rows = session.execute(
        select(ApiKey)
        .where(ApiKey.workspace_id == user.workspace_id)
        .order_by(ApiKey.created_at.desc())
    ).scalars()
    return [_to_response(row) for row in rows]


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description=(
        "Returns the full key exactly once. Anchor stores only its hash, so "
        "this value cannot be shown again — copy it now or create another key."
    ),
)
async def create_key(
    request: Request,
    payload: ApiKeyCreate,
    user: _AdminDep,
    db: OptionalDbSession,
) -> ApiKeyCreatedResponse:
    session = _require_db(db)
    rate_limit.limiter.check(principal_key(request, user.user_id))

    unknown = [s for s in payload.scopes if s not in ALL_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unknown_scope",
                "message": f"Unknown scopes: {', '.join(sorted(unknown))}. "
                f"Valid scopes are: {', '.join(ALL_SCOPES)}.",
            },
        )

    live = len(
        session.execute(
            select(ApiKey.id).where(
                ApiKey.workspace_id == user.workspace_id, ApiKey.revoked_at.is_(None)
            )
        ).all()
    )
    if live >= MAX_KEYS_PER_WORKSPACE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "too_many_keys",
                "message": f"A workspace may hold at most {MAX_KEYS_PER_WORKSPACE} active keys.",
            },
        )

    full, prefix, digest = _mint_key()
    row = ApiKey(
        workspace_id=user.workspace_id,
        created_by_user_id=user.user_id,
        name=payload.name.strip()[:120],
        key_prefix=prefix,
        key_hash=digest,
        scopes=",".join(dict.fromkeys(payload.scopes))[:300],
        expires_at=(
            datetime.now(UTC) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
    )
    session.add(row)
    session.commit()

    audit.record_event(
        session,
        action="apikey.create",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        target_type="api_key",
        target_id=row.id,
        detail={"name": row.name, "scopes": row.scopes, "prefix": prefix},
    )
    response = _to_response(row, user.email)
    return ApiKeyCreatedResponse(**response.model_dump(), key=full)


@router.delete("/{key_id}", summary="Revoke an API key")
async def revoke_key(key_id: str, user: _AdminDep, db: OptionalDbSession) -> dict[str, str]:
    session = _require_db(db)
    row = session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.workspace_id == user.workspace_id)
    ).scalars().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "No such API key."},
        )
    if row.revoked_at is not None:
        return {"status": "already_revoked", "id": key_id}

    row.revoked_at = datetime.now(UTC)
    session.commit()
    audit.record_event(
        session,
        action="apikey.revoke",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        target_type="api_key",
        target_id=key_id,
        detail={"name": row.name},
    )
    return {"status": "revoked", "id": key_id}
