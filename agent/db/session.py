"""Database access for request handlers.

Two shapes are offered on purpose:

* :func:`require_db` — a hard dependency. A route that genuinely cannot work
  without persistence (registering a user, listing a workspace) uses this, so
  a missing ``DATABASE_URL`` surfaces as an explicit 503 instead of a confusing
  failure further down.
* :func:`optional_db` — a best-effort dependency. ``POST /query`` uses this:
  persisting the turn is valuable, but a deployment running without a database
  should still answer questions rather than refuse them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from agent.db.base import get_session_factory
from agent.observability.logger import get_logger

log = get_logger(__name__)

_UNAVAILABLE = {
    "error": "database_unavailable",
    "message": (
        "This feature needs a configured database. Set DATABASE_URL and run "
        "the migrations; see .env.example."
    ),
}


def _unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_UNAVAILABLE)


def database_configured() -> bool:
    """True when a URL is set. Checked before building an engine so a missing
    configuration produces a clear 503 rather than a connection error."""
    from agent.config import get_settings

    return bool(get_settings().DATABASE_URL.strip())


def get_db() -> Iterator[Session]:
    """Yield a session for a route that cannot proceed without persistence."""
    if not database_configured():
        raise _unavailable()
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def optional_db() -> Iterator[Session | None]:
    """Yield a session when one is configured, otherwise ``None``.

    A read-only deployment with no database is a legitimate configuration for
    the read-heavy paths; the caller decides whether ``None`` is fatal.
    """
    if not database_configured():
        yield None
        return
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
OptionalDbSession = Annotated[Session | None, Depends(optional_db)]
