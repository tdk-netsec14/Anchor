"""Engine, session factory and the declarative base.

The schema is owned by Alembic (``migrations/``), never by this module. Nothing
here calls ``create_all``: a process that silently materialised its own tables
would drift from the migrations the moment one of them was edited, and a
production deploy would depend on whichever version of the code happened to
boot first.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agent.config import get_settings
from agent.observability.logger import get_logger

log = get_logger(__name__)


def as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime, assuming UTC for a naive one.

    Every timestamp column is declared ``DateTime(timezone=True)``, so
    PostgreSQL hands back an aware datetime and comparing it with
    ``datetime.now(UTC)`` is fine. SQLite has no native timestamp type and
    drops the offset, so the same column arrives naive — and comparing a naive
    datetime with an aware one raises ``TypeError`` rather than returning a
    wrong answer. Normalising on the way out of the database is what lets the
    same comparison code run on both.

    The values are written in UTC, so a naive value read back is already UTC
    and only needs the marker attached.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

#: Explicit constraint names. Alembic autogenerate needs stable names to diff
#: against, and unnamed constraints produce migrations that cannot be applied
#: idempotently.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _is_sqlite_memory(url: str) -> bool:
    """True for the in-memory SQLite URLs.

    Both spellings mean the same thing — ``sqlite://`` and
    ``sqlite:///:memory:`` — and neither contains a file path, which is what
    distinguishes them from ``sqlite:///anchor.db``. Getting this wrong is
    quiet and severe: without a static pool each thread gets its own empty
    database, so writes vanish the moment the thread changes.
    """
    if not url.startswith("sqlite"):
        return False
    _, _, path = url.partition("://")
    return path in ("", "/", "/:memory:")


def _engine_kwargs(url: str) -> dict[str, Any]:
    """Per-dialect engine options.

    SQLite is only ever the local/test database, and it needs two adjustments
    to behave like the server it stands in for: a static pool so an in-memory
    database survives between sessions and threads, and the foreign-keys
    pragma switched on, which SQLite ignores unless it is set per connection.
    """
    if url.startswith("sqlite"):
        if not _is_sqlite_memory(url):
            return {"connect_args": {"check_same_thread": False}, "future": True}
        return {
            "connect_args": {"check_same_thread": False},
            # One connection for the whole process, so every session and thread
            # sees the same in-memory database.
            "poolclass": StaticPool,
            "future": True,
        }
    return {
        # The pool is sized for a container, not a laptop: pre-ping rides out
        # the connection drops a managed database takes during maintenance.
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle": 1800,
        "future": True,
    }


def get_engine() -> Engine:
    """The process-wide engine. Created on first use."""
    global _engine
    if _engine is None:
        url = get_settings().DATABASE_URL
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. Anchor's persistent features need a "
                "database; see .env.example."
            )
        kwargs = _engine_kwargs(url)
        _engine = create_engine(url, **kwargs)
        if _engine.dialect.name == "sqlite":
            # SQLite ignores foreign keys unless asked per connection. Without
            # this the ON DELETE CASCADE in the schema is decorative and a
            # deleted workspace would leave its rows behind.
            @event.listens_for(_engine, "connect")
            def _enable_foreign_keys(dbapi_connection, _record):  # noqa: ANN001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        log.info("db.engine_created", context={"dialect": _engine.dialect.name})
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope around a series of operations.

    Commits on success, rolls back on any exception, and always closes. Used by
    the background worker and by scripts, which have no request to hang a
    dependency off.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine and factory. Tests call this after patching
    ``DATABASE_URL``; production never needs it."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
