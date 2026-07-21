"""Resolving the caller, and deciding what they may do.

The single rule this module enforces is that a request only ever acts inside
one workspace, and that the workspace comes from the credential rather than
from the request body, the URL, or a header the client chooses freely.

Three credential kinds resolve to a :class:`Principal`:

* a **demo token** — the development ``POST /auth/token`` shortcut. It names no
  workspace, so it is refused outright by anything tenant-scoped. It cannot be
  minted when ``ENVIRONMENT`` is ``prod``.
* an **access token** — from the real login flow. Carries a workspace and a role
  in it, signed, so authorisation is decided from the signature alone.
* an **API key** — ``ank_...``, looked up by hash, bound to one workspace.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.auth.tokens import TokenClaims, TokenError, decode_access_token
from agent.db.base import as_utc
from agent.db.models import WorkspaceRole
from agent.db.session import optional_db
from agent.observability.logger import get_logger

log = get_logger(__name__)

Role = Literal["admin", "user"]

# auto_error=False so a missing header produces Anchor's error shape rather
# than FastAPI's default 403 body.
_bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    """The authenticated principal attached to a request.

    ``workspace_id`` is ``None`` only for a demo token. Every tenant-scoped
    route checks it, which is why a demo token cannot reach tenant data.
    """

    user_id: str
    #: The signed role claim. Retained for the development role check; the real
    #: authority is ``workspace_role``.
    role: Role = "user"
    email: str | None = None
    workspace_id: str | None = None
    workspace_role: WorkspaceRole | None = None
    session_id: str | None = None
    #: True when authenticated with a workspace API key rather than a user
    #: session. Used only for the "last used" bookkeeping.
    is_api_key: bool = False
    is_demo: bool = False

    # -- capability checks -------------------------------------------------
    def has_role(self, minimum: WorkspaceRole) -> bool:
        """True when this principal meets or exceeds ``minimum`` in its workspace.

        A demo token never does: it has no workspace, so it holds no
        workspace-scoped authority at all.
        """
        if self.workspace_role is None:
            return False
        return self.workspace_role.rank >= minimum.rank

    @property
    def is_authenticated_user(self) -> bool:
        return not self.is_demo


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail={"error": code, "message": message}
    )


def _principal_from_claims(claims: TokenClaims) -> CurrentUser:
    return CurrentUser(
        user_id=claims.subject,
        role=claims.role,  # type: ignore[arg-type]
        email=claims.email,
        workspace_id=claims.workspace_id,
        workspace_role=claims.workspace_role,
        session_id=claims.session_id,
        is_demo=claims.is_demo,
    )


def _principal_from_api_key(raw_key: str, db: Session) -> CurrentUser:
    """Resolve an ``ank_`` key to the workspace it was issued for."""
    from datetime import UTC, datetime

    from agent.db.models import ApiKey
    from agent.security import hash_token

    record = db.query(ApiKey).filter(ApiKey.key_hash == hash_token(raw_key)).one_or_none()
    if record is None:
        raise _unauthorized("invalid_api_key", "The API key is not valid.")
    if record.revoked_at is not None:
        raise _unauthorized("api_key_revoked", "The API key has been revoked.")
    if record.expires_at is not None and as_utc(record.expires_at) <= datetime.now(UTC):
        raise _unauthorized("api_key_expired", "The API key has expired.")

    # Best-effort: a failure to record usage must not reject a valid key.
    record.last_used_at = datetime.now(UTC)
    try:
        db.commit()
    except Exception:  # pragma: no cover - telemetry must not gate access
        db.rollback()

    return CurrentUser(
        user_id=record.created_by_user_id,
        role="user",
        workspace_id=record.workspace_id,
        workspace_role=WorkspaceRole.ADMIN,
        is_api_key=True,
    )


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session | None, Depends(optional_db)],
) -> CurrentUser:
    """Resolve the caller from a bearer credential.

    The membership re-check needs a database, and a deployment without one
    cannot have issued a real token in the first place — so the two situations
    are kept apart rather than conflated:

    * a **real** token with no database reachable is a misconfiguration, and
      is refused with 503 rather than trusted on its signature alone;
    * a **demo** token needs no database, because it grants no tenant access.
      This is the development path, and ``ENVIRONMENT=prod`` refuses to mint
      one at all.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing_token", "A bearer token is required.")
    if (credentials.scheme or "").lower() != "bearer":
        raise _unauthorized("invalid_token", "Only the Bearer scheme is supported.")

    raw = credentials.credentials

    if raw.startswith("ank_"):
        if db is None:
            raise _unauthorized("invalid_api_key", "The API key is not valid.")
        return _principal_from_api_key(raw, db)

    try:
        claims = decode_access_token(raw)
    except TokenError as exc:
        raise exc.to_http() from exc

    principal = _principal_from_claims(claims)
    if not principal.is_demo:
        if db is None:
            log.error(
                "auth.database_unavailable_for_authenticated_request",
                context={"user_id": principal.user_id},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "database_unavailable",
                    "message": "Authentication is temporarily unavailable. Please try again.",
                },
            )
        _assert_membership_is_current(principal, db, request)
    return principal


def _assert_membership_is_current(principal: CurrentUser, db: Session, request: Request) -> None:
    """Fail closed when the credential no longer matches the database.

    The signed claim is what authorises a request, but a member removed five
    minutes ago still holds a valid signature. Re-reading the membership turns
    a revoked permission into an immediate 403 instead of one that lingers for
    the remainder of the token's lifetime.
    """
    from agent.db.models import Membership, User

    membership = db.scalars(
        select(Membership).where(
            Membership.user_id == principal.user_id,
            Membership.workspace_id == principal.workspace_id,
        )
    ).one_or_none()
    if membership is None:
        log.warning(
            "authz.membership_revoked",
            context={
                "user_id": principal.user_id,
                "workspace_id": principal.workspace_id,
                "path": request.url.path,
            },
        )
        raise _forbidden("not_a_member", "You are not a member of this workspace.")
    if membership.role != principal.workspace_role:
        raise _forbidden("role_changed", "Your permissions have changed. Sign in again.")

    user = db.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise _forbidden("account_disabled", "This account is not active.")


def require_role(role: Role):
    """Build a dependency enforcing the development ``admin``/``user`` split.

    Kept for the existing ingestion routes, which predate workspaces. It passes
    for a demo admin token *and* for a real principal whose workspace role is
    OWNER or ADMIN, so a workspace administrator can manage the knowledge base
    without the two systems disagreeing about who is in charge.
    """

    async def _dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.is_demo:
            allowed = user.role == role
        else:
            # A viewer is not a member for the purpose of writes, so the floor
            # for any legacy admin-gated route is MEMBER.
            floor = WorkspaceRole.ADMIN if role == "admin" else WorkspaceRole.MEMBER
            allowed = user.has_role(floor)
        if not allowed:
            log.warning(
                "authz.denied",
                context={
                    "required_role": role,
                    "actual_role": user.role,
                    "workspace_role": user.workspace_role.value if user.workspace_role else None,
                    "user_id": user.user_id,
                },
            )
            raise _forbidden("insufficient_role", f"This action requires the '{role}' role.")
        return user

    return _dependency


def require_workspace_role(minimum: WorkspaceRole):
    """Build a dependency requiring at least ``minimum`` in the active workspace.

    Unlike :func:`require_role` this has no demo path at all — a token that
    names no workspace cannot satisfy a requirement about a workspace.
    """

    async def _dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.workspace_id is None:
            raise _forbidden(
                "no_workspace",
                "This action is scoped to a workspace, and the credential names none.",
            )
        if not user.has_role(minimum):
            log.warning(
                "authz.workspace_role_denied",
                context={
                    "required": minimum.value,
                    "actual": user.workspace_role.value if user.workspace_role else None,
                    "user_id": user.user_id,
                    "workspace_id": user.workspace_id,
                },
            )
            raise _forbidden(
                "insufficient_role", f"This action requires the '{minimum.value}' role or higher."
            )
        return user

    return _dependency


def require_active_workspace(user: CurrentUser) -> str:
    """Return the caller's workspace id, or refuse.

    Used by every tenant-scoped route that takes the workspace from the
    credential rather than the URL. Raising here, in one place, is what stops a
    route from quietly operating across all tenants.
    """
    if not user.workspace_id:
        raise _forbidden(
            "no_workspace",
            "This action is scoped to a workspace, and the credential names none.",
        )
    return user.workspace_id


def deny_unknown_workspace(user: CurrentUser, requested: str | None) -> str:
    """Guard routes whose URL names a workspace.

    The credential is authoritative. A mismatch is refused rather than
    reinterpreted, so a caller cannot read workspace B by asking for it in the
    path while presenting a token for workspace A.
    """
    active = require_active_workspace(user)
    if requested is not None and requested != active:
        raise _forbidden(
            "workspace_mismatch",
            "You do not have access to that workspace.",
        )
    return active


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminUserDep = Annotated[CurrentUser, Depends(require_role("admin"))]


def optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser | None:
    """The caller if one is present, else ``None``. Never raises.

    For endpoints that change shape when signed in but do not require it.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return _principal_from_claims(decode_access_token(credentials.credentials))
    except TokenError:
        return None
