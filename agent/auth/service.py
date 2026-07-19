"""Account and session lifecycle.

Registration, login, refresh, logout and password reset. The routes in
``agent/routers/auth.py`` are a thin shell over this module so the rules — token
rotation, pepper use, what a failed login reveals — live in one place and can
be tested without HTTP.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.auth.tokens import create_access_token
from agent.config import get_settings
from agent.db.base import as_utc
from agent.db.models import (
    Membership,
    PasswordResetToken,
    RefreshToken,
    User,
    Workspace,
    WorkspaceRole,
)
from agent.observability.logger import get_logger
from agent.security import (
    generate_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

log = get_logger(__name__)


class AuthError(Exception):
    """A credential or account rule was not satisfied.

    ``code`` is stable and safe to return. The message never distinguishes
    "no such user" from "wrong password".
    """


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def normalise_email(raw: str) -> str:
    email = unicodedata.normalize("NFKC", raw).strip().lower()
    if not email or len(email) > 320 or not _EMAIL_RE.match(email):
        raise AuthError("invalid_email", "Enter a valid email address.")
    return email


def validate_password(password: str) -> None:
    settings = get_settings()
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        raise AuthError(
            "weak_password",
            f"Use at least {settings.PASSWORD_MIN_LENGTH} characters.",
        )
    if len(password) > settings.PASSWORD_MAX_LENGTH:
        # Argon2 takes the cost of the whole input, so an unbounded password is
        # a cheap way to make a login expensive.
        raise AuthError(
            "password_too_long",
            f"Use at most {settings.PASSWORD_MAX_LENGTH} characters.",
        )


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKC", name).strip().lower())
    return (base.strip("-") or "workspace")[:70]


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    candidate = base
    for _ in range(50):
        exists = db.execute(select(Workspace.id).where(Workspace.slug == candidate)).first()
        if exists is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(3)}"
    return f"{base}-{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str = "",
    workspace_name: str | None = None,
) -> tuple[User, Workspace]:
    """Create an account and its first workspace, with the user as OWNER.

    The account and its workspace are created together: an account with no
    workspace has nowhere to put a document and would be useless on first login.
    """
    settings = get_settings()
    if not settings.AUTH_ALLOW_REGISTRATION:
        raise AuthError(
            "registration_closed",
            "Registration is closed on this deployment. Ask an administrator for an invitation.",
        )

    normalised = normalise_email(email)
    validate_password(password)

    if db.execute(select(User.id).where(User.email == normalised)).first():
        raise AuthError("email_taken", "An account with that email already exists.")

    user = User(
        email=normalised,
        password_hash=hash_password(password),
        full_name=full_name.strip()[:120],
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        # Two simultaneous registrations for one address: the loser gets the
        # same answer as any other duplicate rather than a 500.
        db.rollback()
        raise AuthError("email_taken", "An account with that email already exists.") from exc

    workspace = create_workspace(
        db,
        name=(workspace_name or "").strip() or f"{user.full_name or normalised} workspace"[:120],
        owner_email=normalised,
        is_personal=True,
    )
    add_member(db, workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
    db.commit()
    log.info("auth.registered", context={"user_id": user.id, "workspace_id": workspace.id})
    return user, workspace


def create_workspace(
    db: Session,
    *,
    name: str,
    owner_email: str,
    is_personal: bool = False,
) -> Workspace:
    workspace = Workspace(
        name=name[:120],
        slug=unique_slug(db, name),
        owner_email=owner_email[:320],
        is_personal=is_personal,
    )
    db.add(workspace)
    db.flush()
    return workspace


def add_member(db: Session, *, workspace_id: str, user_id: str, role: WorkspaceRole) -> Membership:
    membership = Membership(workspace_id=workspace_id, user_id=user_id, role=role)
    db.add(membership)
    db.flush()
    return membership


# ---------------------------------------------------------------------------
# Login and sessions
# ---------------------------------------------------------------------------
def authenticate(db: Session, *, email: str, password: str) -> User:
    """Verify a password. Raises :class:`AuthError` for every failure mode.

    A missing account and a wrong password produce the same code and message, so
    the endpoint cannot be used to enumerate registered addresses. A dummy
    verification runs in the missing-account case so the two paths take
    comparable time.
    """
    try:
        normalised = normalise_email(email)
    except AuthError as exc:
        raise AuthError(
            "invalid_credentials", "That email and password combination is not valid."
        ) from exc

    user = db.scalars(select(User).where(User.email == normalised)).one_or_none()
    if user is None:
        # Constant-ish work on the miss path; the hash is discarded.
        verify_password(password, _DUMMY_HASH)
        raise AuthError("invalid_credentials", "That email and password combination is not valid.")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid_credentials", "That email and password combination is not valid.")
    if not user.is_active:
        raise AuthError("account_disabled", "This account is not active.")

    if needs_rehash(user.password_hash):
        # Parameters were raised since this password was set. Re-hash now, on
        # the one request where the plaintext is legitimately available.
        user.password_hash = hash_password(password)
        log.info("auth.password_rehashed", context={"user_id": user.id})

    user.last_login_at = datetime.now(UTC)
    db.commit()
    return user


#: A valid Argon2 hash of a random string, used only to equalise timing on the
#: "no such user" path. It is not a credential for any account.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=2$c2VjdXJpdHNhbHRzb21lc2FsdA$Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4"
)


def active_workspace(db: Session, user_id: str, requested: str | None = None) -> Workspace:
    """The workspace to act in for this request.

    A caller may name one; otherwise the user's first workspace is used, which
    makes a single-workspace account work with no extra configuration.
    """
    if requested:
        workspace = db.get(Workspace, requested)
        member = db.scalars(
            select(Membership).where(
                Membership.user_id == user_id, Membership.workspace_id == requested
            )
        ).one_or_none()
        if workspace is None or member is None:
            raise AuthError("workspace_not_found", "You do not have access to that workspace.")
        return workspace

    workspace = db.scalars(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at)
        .limit(1)
    ).first()
    if workspace is None:
        raise AuthError("no_workspace", "This account is not a member of any workspace.")
    return workspace


def membership_role(db: Session, user_id: str, workspace_id: str) -> WorkspaceRole:
    member = db.scalars(
        select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
    ).one_or_none()
    if member is None:
        raise AuthError("not_a_member", "You are not a member of this workspace.")
    return member.role


class IssuedSession:
    __slots__ = ("access_token", "refresh_token", "expires_in", "user", "workspace", "role")

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        user: User,
        workspace: Workspace,
        role: WorkspaceRole,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.user = user
        self.workspace = workspace
        self.role = role


def issue_session(
    db: Session,
    user: User,
    workspace: Workspace,
    role: WorkspaceRole,
    *,
    user_agent: str = "",
) -> IssuedSession:
    """Mint an access/refresh pair bound to one workspace and role.

    The access token is short-lived and self-contained; the refresh token is
    opaque, stored hashed, and is the only thing that can produce a new one.
    """
    settings = get_settings()
    raw_refresh = generate_token()
    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        expires_at=datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS),
        user_agent=user_agent[:200],
    )
    db.add(record)
    db.flush()

    access, expires_in = create_access_token(
        user.id,
        # The legacy claim is always the floor. Authority comes from `wrole`,
        # so a viewer is never mistaken for an administrator by older code.
        "user",
        email=user.email,
        workspace_id=workspace.id,
        workspace_role=role,
        session_id=record.id,
    )
    db.commit()
    return IssuedSession(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        user=user,
        workspace=workspace,
        role=role,
    )


def rotate_refresh_token(db: Session, raw_refresh: str, *, user_agent: str = "") -> IssuedSession:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Single-use. A token that is presented twice — the signature of a stolen
    copy — is refused the second time, and every session for that user is
    revoked so the theft is visible and recoverable.
    """
    now = datetime.now(UTC)
    record = db.scalars(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh))
    ).one_or_none()
    if record is None:
        raise AuthError("invalid_refresh_token", "The session is not valid. Sign in again.")
    if record.revoked_at is not None:
        log.warning("auth.refresh_token_reuse", context={"user_id": record.user_id})
        revoke_all_sessions(db, record.user_id)
        raise AuthError("invalid_refresh_token", "The session is not valid. Sign in again.")
    if as_utc(record.expires_at) <= now:
        raise AuthError("refresh_token_expired", "The session has expired. Sign in again.")

    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AuthError("account_disabled", "This account is not active.")

    workspace = active_workspace(db, user.id)
    role = membership_role(db, user.id, workspace.id)

    record.revoked_at = now
    db.commit()

    return issue_session(db, user, workspace, role, user_agent=user_agent)


def revoke_refresh_token(db: Session, raw_refresh: str) -> bool:
    record = db.scalars(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh))
    ).one_or_none()
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = datetime.now(UTC)
    db.commit()
    return True


def revoke_all_sessions(db: Session, user_id: str) -> int:
    rows = db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars()
    count = 0
    for row in rows:
        row.revoked_at = datetime.now(UTC)
        count += 1
    db.commit()
    return count


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def create_password_reset(db: Session, user: User) -> str:
    """Create a reset token and return it.

    The caller is responsible for delivering it. Nothing is emailed by Anchor:
    a deployment that wants email wires its own provider to the returned token.
    """
    settings = get_settings()
    raw = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.PASSWORD_RESET_EXPIRY_MINUTES),
        )
    )
    db.commit()
    return raw


def consume_password_reset(db: Session, raw_token: str, new_password: str) -> None:
    """Redeem a reset token and set the new password."""
    validate_password(new_password)
    now = datetime.now(UTC)
    record = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    ).one_or_none()
    if record is None or record.used_at is not None or as_utc(record.expires_at) <= now:
        raise AuthError("invalid_reset_token", "That reset link is no longer valid.")

    user = db.get(User, record.user_id)
    if user is None:
        raise AuthError("invalid_reset_token", "That reset link is no longer valid.")

    user.password_hash = hash_password(new_password)
    record.used_at = now
    db.commit()
    # A password change must end every existing session, or an attacker who
    # holds a stolen refresh token keeps it after the owner has recovered.
    revoke_all_sessions(db, user.id)
    log.info("auth.password_reset", context={"user_id": user.id})
