"""JWT issuance and verification.

Two kinds of token exist and they are not interchangeable:

* a **demo token**, minted by ``POST /auth/token`` from a username and a
  self-selected role with no password behind it. It carries a ``role`` claim
  and nothing else. The endpoint refuses to run when ``ENVIRONMENT`` is ``prod``.
* an **access token**, minted by the real login flow. It carries the user id,
  the active workspace and that user's role *in that workspace*, so an
  authorization decision can be made from the signature alone.

Both are signed with the same secret and both are accepted by
:func:`decode_access_token`, because the role check is written once and has to
keep working for the development path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status

from agent.config import get_settings
from agent.db.models import WorkspaceRole
from agent.observability.logger import get_logger

log = get_logger(__name__)

#: The role claim every token must carry, so the decoder's `require` list stays
#: satisfied for both token kinds.
Role = str

#: Marks a token minted by the real login flow. Its absence is what makes a
#: token a demo token.
ACCESS_TOKEN_TYPE = "access"

_LEGACY_ROLES = ("admin", "user")


class TokenError(Exception):
    """A token was rejected. ``code`` is a stable, non-oracular identifier."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_http(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": self.code, "message": self.message},
            headers={"WWW-Authenticate": "Bearer"},
        )


class TokenClaims:
    """The verified contents of a token.

    ``workspace_role`` is ``None`` for a demo token, which is exactly how the
    authorisation code tells the two apart: a demo token has no workspace, so it
    cannot be granted workspace-scoped authority.
    """

    __slots__ = (
        "subject",
        "role",
        "email",
        "workspace_id",
        "workspace_role",
        "session_id",
        "is_demo",
    )

    def __init__(
        self,
        subject: str,
        role: Role,
        *,
        email: str | None = None,
        workspace_id: str | None = None,
        workspace_role: WorkspaceRole | None = None,
        session_id: str | None = None,
        is_demo: bool = False,
    ) -> None:
        self.subject = subject
        self.role = role
        self.email = email
        self.workspace_id = workspace_id
        self.workspace_role = workspace_role
        self.session_id = session_id
        self.is_demo = is_demo


def create_access_token(
    subject: str,
    role: Role,
    expires_minutes: int | None = None,
    *,
    email: str | None = None,
    workspace_id: str | None = None,
    workspace_role: WorkspaceRole | None = None,
    session_id: str | None = None,
) -> tuple[str, int]:
    """Sign a token. Returns it and its lifetime in seconds.

    With no keyword arguments this produces the development token: a ``sub``,
    a ``role`` and the standard time claims, and nothing that names a tenant.
    """
    settings = get_settings()
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRY_MINUTES
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iss": settings.JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    # The real flow always supplies a workspace, so the token is self-describing
    # and the authoriser never has to guess which tenant it speaks for.
    if workspace_id is not None:
        payload["typ"] = ACCESS_TOKEN_TYPE
        payload["wid"] = workspace_id
        payload["wrole"] = workspace_role.value if workspace_role else None
        payload["sid"] = session_id
        if email:
            payload["email"] = email
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, minutes * 60


def decode_access_token(token: str) -> TokenClaims:
    """Verify a token and return its claims.

    Raises :class:`TokenError` for anything invalid. The specific reason is not
    returned beyond a stable code, so this cannot be used as an oracle to
    distinguish a forged signature from an expired one.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired", "The access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid_token", "The access token is not valid.") from exc

    role = payload.get("role")
    if role not in _LEGACY_ROLES:
        raise TokenError("invalid_token", "The access token carries an unknown role.")

    workspace_id = payload.get("wid")
    if workspace_id is None:
        return TokenClaims(subject=str(payload["sub"]), role=role, is_demo=True)

    raw_role = payload.get("wrole")
    try:
        workspace_role = WorkspaceRole(raw_role) if raw_role else None
    except ValueError as exc:
        raise TokenError(
            "invalid_token", "The access token carries an unknown workspace role."
        ) from exc
    if workspace_role is None:
        raise TokenError("invalid_token", "The access token is missing a workspace role.")

    return TokenClaims(
        subject=str(payload["sub"]),
        role=role,
        email=payload.get("email"),
        workspace_id=str(workspace_id),
        workspace_role=workspace_role,
        session_id=payload.get("sid"),
        is_demo=False,
    )
