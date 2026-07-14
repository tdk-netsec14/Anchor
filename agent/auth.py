"""JWT issuance, verification and role-based access control.

DEMO SIMPLIFICATION: ``POST /auth/token`` mints a token straight from a
username and role with no password check and no user database. That is a
deliberate scope decision for a portfolio system, not an oversight — the
interesting engineering is in the *verification* path, and wiring a real
identity provider in front of this would change nothing about it. See the
"Known simplifications" section of the README before putting this on a network
you care about.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from agent.config import get_settings
from agent.observability.logger import get_logger

log = get_logger(__name__)

Role = Literal["admin", "user"]

router = APIRouter(tags=["auth"])

# auto_error=False so a missing header produces our error shape rather than
# FastAPI's default 403 body.
_bearer = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class TokenRequest(BaseModel):
    """Credentials for the demo token endpoint.

    There is no password field on purpose; see the module docstring.
    """

    username: str = Field(min_length=1, max_length=120, examples=["alice"])
    role: Role = Field(default="user", examples=["user"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")
    role: Role


class CurrentUser(BaseModel):
    """The authenticated principal attached to every protected request."""

    user_id: str
    role: Role


# --------------------------------------------------------------------------
# Token operations
# --------------------------------------------------------------------------
def create_access_token(
    subject: str, role: Role, expires_minutes: int | None = None
) -> tuple[str, int]:
    """Sign a JWT. Returns the token and its lifetime in seconds."""
    settings = get_settings()
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRY_MINUTES
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iss": settings.JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, minutes * 60


def decode_token(token: str) -> CurrentUser:
    """Verify a JWT and return its principal.

    Raises :class:`HTTPException` 401 for anything invalid. The specific reason
    is not returned to the caller beyond a stable code, to avoid handing an
    attacker a token oracle.
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
        raise _unauthorized("token_expired", "The access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("invalid_token", "The access token is not valid.") from exc

    role = payload.get("role")
    if role not in ("admin", "user"):
        raise _unauthorized("invalid_token", "The access token carries an unknown role.")
    return CurrentUser(user_id=str(payload["sub"]), role=role)


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Resolve the caller, or reject the request."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing_token", "A bearer token is required.")
    if (credentials.scheme or "").lower() != "bearer":
        raise _unauthorized("invalid_token", "Only the Bearer scheme is supported.")
    return decode_token(credentials.credentials)


def require_role(role: Role):
    """Build a dependency that also enforces a role.

    Used as ``Depends(require_role("admin"))`` on the ingestion route so that a
    normal user can never write to the knowledge base.
    """

    async def _dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role != role:
            log.warning(
                "authz.denied",
                context={"required_role": role, "actual_role": user.role, "user_id": user.user_id},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_role",
                    "message": f"This action requires the '{role}' role.",
                },
            )
        return user

    return _dependency


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminUserDep = Annotated[CurrentUser, Depends(require_role("admin"))]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="Issue a demo access token",
    description=(
        "Exchanges a username and role for a signed JWT.\n\n"
        "**Demo only** — there is no password verification and no user "
        "database behind this endpoint. Replace it with your identity provider "
        "before any real deployment."
    ),
)
async def issue_token(payload: TokenRequest) -> TokenResponse:
    token, expires_in = create_access_token(payload.username, payload.role)
    log.info(
        "auth.token_issued",
        context={"user_id": payload.username, "role": payload.role},
    )
    return TokenResponse(access_token=token, expires_in=expires_in, role=payload.role)


def _main(argv: list[str] | None = None) -> int:
    """Developer convenience: print a token to stdout.

    Reached with ``python -m agent.auth <username> <role>``, which is what
    `make token` uses. Equivalent to POSTing to /auth/token.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m agent.auth", description="Issue a demo Anchor JWT."
    )
    parser.add_argument("username", nargs="?", default="demo")
    parser.add_argument("role", nargs="?", default="user", choices=["admin", "user"])
    parser.add_argument("--expires-minutes", type=int, default=None)
    args = parser.parse_args(argv)

    token, expires_in = create_access_token(
        args.username, args.role, expires_minutes=args.expires_minutes
    )
    print(json.dumps(TokenResponse(access_token=token, expires_in=expires_in, role=args.role).model_dump(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
