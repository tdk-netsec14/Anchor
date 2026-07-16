"""First-run provisioning.

A fresh database has nobody in it, which means nobody can log in. This module
creates the initial account described by ``BOOTSTRAP_ADMIN_EMAIL`` and
``BOOTSTRAP_ADMIN_PASSWORD``, so a new deployment has a way in without anyone
having to open a shell on the server.

It is idempotent and it never overwrites an existing account: running it twice
changes nothing, and running it against a populated database is a no-op. It
refuses to run in production unless the password is set, because an
auto-provisioned account with a guessed password is exactly the thing this
project is supposed to avoid.

    python -m agent.db.seed
"""

from __future__ import annotations

import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.auth.service import AuthError, register_user
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import User, Workspace
from agent.observability.logger import configure_logging, get_logger

log = get_logger(__name__)


def seed(session: Session) -> tuple[User, Workspace] | None:
    """Create the bootstrap account if it is missing. Returns it, or ``None``
    when there was nothing to do."""
    settings = get_settings()

    if session.execute(select(func.count(User.id))).scalar_one() > 0:
        log.info("seed.skipped", context={"reason": "database already has users"})
        return None

    email = settings.BOOTSTRAP_ADMIN_EMAIL.strip().lower()
    password = settings.BOOTSTRAP_ADMIN_PASSWORD
    if not email or not password:
        log.info(
            "seed.skipped",
            context={
                "reason": (
                    "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD are not both set"
                )
            },
        )
        return None

    try:
        user, workspace = register_user(
            session,
            email=email,
            password=password,
            full_name="Workspace Owner",
            workspace_name="Primary",
        )
    except AuthError as exc:
        log.error("seed.failed", context={"reason": exc.code})
        return None

    log.info(
        "seed.created",
        context={"user_id": user.id, "workspace_id": workspace.id, "email": user.email},
    )
    return user, workspace


def _main() -> int:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)

    if not settings.DATABASE_URL:
        print(
            "DATABASE_URL is not set. Copy .env.example to .env, set it, and run "
            "`alembic upgrade head` first.",
            file=sys.stderr,
        )
        return 1

    if settings.ENVIRONMENT == "prod" and not settings.BOOTSTRAP_ADMIN_PASSWORD:
        print(
            "Refusing to seed in production without BOOTSTRAP_ADMIN_PASSWORD set.",
            file=sys.stderr,
        )
        return 1

    with session_scope() as session:
        result = seed(session)

    if result is None:
        print("Nothing to do: the database already has users, or no bootstrap account is configured.")
        return 0

    user, workspace = result
    print(f"Created {user.email} as OWNER of workspace '{workspace.name}' ({workspace.slug}).")
    print("Sign in with that address and the password you configured. Change it after the first login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
