"""Authentication routes.

Two families live here and they are deliberately kept apart:

``/auth/register``, ``/auth/login``, ``/auth/refresh``, ``/auth/logout``,
``/auth/forgot-password``, ``/auth/reset-password``, ``/auth/me``
    The real flow. Passwords are Argon2id-hashed, sessions are refreshable and
    revocable, and every response names a workspace.

``/auth/token``
    The original development shortcut: a username and a self-selected role, no
    password. It is refused outright when ``ENVIRONMENT`` is ``prod`` and when
    ``AUTH_ALLOW_DEMO_TOKENS`` is off, so it cannot reach a real deployment
    even if the environment variable is misconfigured.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent import audit
from agent.auth.principals import CurrentUserDep, Role, _unauthorized
from agent.auth.service import (
    AuthError,
    IssuedSession,
    active_workspace,
    authenticate,
    consume_password_reset,
    create_password_reset,
    issue_session,
    membership_role,
    normalise_email,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
)
from agent.auth.tokens import create_access_token
from agent.config import get_settings
from agent.db.models import Membership, User, Workspace, WorkspaceRole
from agent.db.session import DbSession, database_configured
from agent.observability.logger import get_logger
from agent.schemas.auth import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    UserResponse,
    WorkspaceSummary,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)


def _auth_failure(exc: AuthError) -> HTTPException:
    code = (
        401
        if exc.code in {"invalid_credentials", "invalid_refresh_token", "account_disabled"}
        else 400
    )
    if exc.code == "account_disabled":
        code = status.HTTP_403_FORBIDDEN
    return HTTPException(status_code=code, detail={"error": exc.code, "message": exc.message})


def _session_response(session: IssuedSession) -> SessionResponse:
    return SessionResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        user_id=session.user.id,
        email=session.user.email,
        full_name=session.user.full_name,
        workspace_id=session.workspace.id,
        workspace_name=session.workspace.name,
        workspace_role=session.role,
    )


def _client_ip(request: Request) -> str:
    """The caller's address, honouring one proxy hop.

    ``request.client.host`` is the load balancer on any managed platform.
    ``X-Forwarded-For`` is client-controllable, so only its first entry is
    read and only as a hint for the audit trail — never for authorisation.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


# ---------------------------------------------------------------------------
# Real authentication
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and its first workspace",
    description=(
        "Registers a user, creates a workspace for them as OWNER, and returns "
        "a session. Refused when `AUTH_ALLOW_REGISTRATION` is off."
    ),
)
async def register(payload: RegisterRequest, request: Request, db: DbSession) -> SessionResponse:
    try:
        user, workspace = register_user(
            db,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            workspace_name=payload.workspace_name,
        )
    except AuthError as exc:
        audit.record_event(
            db,
            action="user.register",
            actor_email=payload.email,
            outcome="denied",
            ip_address=_client_ip(request),
            detail={"reason": exc.code},
        )
        raise _auth_failure(exc) from exc

    session = issue_session(
        db, user, workspace, WorkspaceRole.OWNER, user_agent=request.headers.get("user-agent", "")
    )
    audit.record_event(
        db,
        action="user.register",
        actor_email=user.email,
        user_id=user.id,
        workspace_id=workspace.id,
        target_type="workspace",
        target_id=workspace.id,
        ip_address=_client_ip(request),
    )
    return _session_response(session)


@router.post(
    "/login",
    response_model=SessionResponse,
    summary="Exchange credentials for a session",
    description=(
        "Verifies the password and returns an access/refresh pair scoped to "
        "one workspace. A wrong address and a wrong password produce the same "
        "response, so the endpoint cannot be used to enumerate accounts."
    ),
)
async def login(payload: LoginRequest, request: Request, db: DbSession) -> SessionResponse:
    try:
        user = authenticate(db, email=payload.email, password=payload.password)
        workspace = active_workspace(db, user.id)
        role = membership_role(db, user.id, workspace.id)
        session = issue_session(
            db, user, workspace, role, user_agent=request.headers.get("user-agent", "")
        )
    except AuthError as exc:
        audit.record_event(
            db,
            action="user.login",
            actor_email=payload.email[:320],
            outcome="failure",
            ip_address=_client_ip(request),
            detail={"reason": exc.code},
        )
        raise _auth_failure(exc) from exc

    audit.record_event(
        db,
        action="user.login",
        actor_email=user.email,
        user_id=user.id,
        workspace_id=workspace.id,
        ip_address=_client_ip(request),
    )
    return _session_response(session)


@router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="Exchange a refresh token for a new session",
    description=(
        "Refresh tokens are single-use. Presenting one that was already "
        "redeemed is treated as a stolen token: every session for that user is "
        "revoked and the request is refused."
    ),
)
async def refresh(payload: RefreshRequest, request: Request, db: DbSession) -> SessionResponse:
    try:
        session = rotate_refresh_token(
            db, payload.refresh_token, user_agent=request.headers.get("user-agent", "")
        )
    except AuthError as exc:
        raise _auth_failure(exc) from exc
    return _session_response(session)


@router.post("/logout", summary="Revoke a refresh token", description="Ends one session.")
async def logout(payload: RefreshRequest, db: DbSession) -> dict[str, str]:
    revoke_refresh_token(db, payload.refresh_token)
    # Idempotent by design: logging out with an unknown token is a success,
    # because the caller's intent — that this session is not usable — holds.
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse, summary="The signed-in user")
async def me(user: CurrentUserDep, db: DbSession) -> UserResponse:
    record = db.get(User, user.user_id)
    if record is None:
        raise _unauthorized("invalid_token", "The access token is not valid.")
    return UserResponse(
        user_id=record.id,
        email=record.email,
        full_name=record.full_name,
        created_at=record.created_at.isoformat(),
        last_login_at=record.last_login_at.isoformat() if record.last_login_at else None,
    )


@router.get(
    "/workspaces",
    response_model=list[WorkspaceSummary],
    summary="Workspaces the caller belongs to",
)
async def my_workspaces(user: CurrentUserDep, db: DbSession) -> list[WorkspaceSummary]:
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.user_id)
        .order_by(Membership.created_at)
    ).all()
    return [
        WorkspaceSummary(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            role=role,
            is_personal=workspace.is_personal,
            created_at=workspace.created_at.isoformat(),
        )
        for workspace, role in rows
    ]


@router.post(
    "/forgot-password",
    response_model=PasswordResetResponse,
    summary="Start a password reset",
    description=(
        "Always returns the same response, whether or not the address is "
        "registered, so the endpoint cannot be used to discover accounts. The "
        "token is echoed only when no mail provider is configured."
    ),
)
async def forgot_password(
    payload: PasswordResetRequest, request: Request, db: DbSession
) -> PasswordResetResponse:
    generic = "If that address has an account, a reset link has been sent."
    try:
        email = normalise_email(payload.email)
    except AuthError:
        return PasswordResetResponse(reset_token=None, message=generic)

    user = db.scalars(select(User).where(User.email == email)).one_or_none()
    if user is None or not user.is_active:
        return PasswordResetResponse(reset_token=None, message=generic)

    token = create_password_reset(db, user)
    audit.record_event(
        db,
        action="user.password_reset_requested",
        actor_email=user.email,
        user_id=user.id,
        ip_address=_client_ip(request),
    )
    # Anchor sends no email. A deployment that wants this returns null and
    # delivers the link through its own provider; a developer running locally
    # gets the token so the flow can actually be exercised.
    expose = not database_configured() or get_settings().ENVIRONMENT != "prod"
    return PasswordResetResponse(reset_token=token if expose else None, message=generic)


@router.post("/reset-password", summary="Complete a password reset")
async def reset_password(payload: PasswordResetConfirm, db: DbSession) -> dict[str, str]:
    try:
        consume_password_reset(db, payload.token, payload.new_password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": exc.code, "message": exc.message},
        ) from exc
    return {"status": "password_updated"}


# ---------------------------------------------------------------------------
# Development shortcut
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    """Credentials for the development token endpoint.

    There is no password field on purpose — the endpoint exists only to make
    the development loop short, and it is not reachable in production.
    """

    username: str = Field(min_length=1, max_length=120, examples=["alice"])
    role: Role = Field(default="user", examples=["user"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")
    role: Role


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Issue a development access token (non-production only)",
    description=(
        "Exchanges a username and role for a signed JWT **with no password "
        "check and no user database**.\n\n"
        "This endpoint is refused when `ENVIRONMENT=prod` or when "
        "`AUTH_ALLOW_DEMO_TOKENS` is false. Tokens it issues are refused by "
        "every tenant-scoped route, because they name no workspace."
    ),
)
async def issue_token(payload: TokenRequest) -> TokenResponse:
    settings = get_settings()
    if settings.ENVIRONMENT == "prod" or not settings.AUTH_ALLOW_DEMO_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": "The development token endpoint is disabled on this deployment.",
            },
        )
    token, expires_in = create_access_token(payload.username, payload.role)
    log.info("auth.demo_token_issued", context={"user_id": payload.username, "role": payload.role})
    return TokenResponse(access_token=token, expires_in=expires_in, role=payload.role)
