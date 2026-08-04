"""Pytest bootstrap.

Environment variables are seeded *before* any application module is imported so
that ``get_settings()`` never fails (or picks up a developer's real ``.env``)
during a test run.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production-0123456789")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("CHROMA_PERSIST_DIR", str(ROOT / ".pytest_chroma"))
os.environ.setdefault("CHROMA_COLLECTION", "anchor_kb_test")
os.environ.setdefault("TICKETS_DIR", str(ROOT / ".pytest_tickets"))
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("ROUTER_FALLBACK_CHAIN", "ollama,groq")

from agent.config import get_settings  # noqa: E402
from agent.observability.metrics import registry  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Keep process-wide state from leaking between tests."""
    get_settings.cache_clear()
    registry.reset()
    yield
    registry.reset()


# ---------------------------------------------------------------------------
# Database-backed tests
# ---------------------------------------------------------------------------
# Anchor's persistence tests run against SQLite in memory. The models, the
# session handling and the tenant predicates are the same code PostgreSQL runs;
# only the driver differs, and the migration round-trip is verified against a
# real PostgreSQL in CI.
#
# The application builds its own engine from DATABASE_URL, exactly as it does
# in production, and the fixture creates the schema on *that* engine. Nothing
# is injected or stubbed, so the code under test is the code that ships.
@pytest.fixture
def saas_env(monkeypatch):
    """A TestClient whose application is backed by a fresh in-memory database.

    Yields ``(client, session)``: the client to drive the API, and a session on
    the same engine to arrange and assert against the rows behind it.
    """
    from fastapi.testclient import TestClient

    import agent.db.models  # noqa: F401  (registers the tables on Base.metadata)
    from agent.config import get_settings
    from agent.db.base import Base, get_engine, get_session_factory, reset_engine
    from agent.main import create_app

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())

    session = get_session_factory()()
    try:
        with TestClient(create_app()) as client:
            yield client, session
    finally:
        session.close()
        reset_engine()


@pytest.fixture
def app_client(saas_env):
    return saas_env[0]


@pytest.fixture
def db_session(saas_env):
    return saas_env[1]


@pytest.fixture
def registered(app_client):
    """Register an owner through the API and return their session.

    Driving the real endpoint means password hashing, workspace creation and
    session issuance are covered by every test that uses this, rather than each
    test building its own shortcut past them.
    """
    session = register(app_client, "owner@example.com", workspace_name="Acme")
    session["headers"] = {"Authorization": f"Bearer {session['access_token']}"}
    return session


def register(client, email: str, *, password: str = "correct-horse-battery-staple", **extra):
    """Register a user and return the session body, asserting it succeeded."""
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": email.split("@")[0].title(),
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return body


def login(client, email: str, password: str = "correct-horse-battery-staple"):
    """Log in and return the session body, asserting it succeeded."""
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    body["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return body


@pytest.fixture(scope="session")
def settings():
    return get_settings()


class _JsonCaptureHandler(logging.Handler):
    """Collects log records exactly as Anchor's JSON formatter renders them.

    pytest's own ``caplog`` captures raw ``LogRecord`` objects, so it can only
    see the unformatted message. These tests assert on the JSON that actually
    ships, so they need the real formatter.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        from agent.observability.logger import JsonFormatter

        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover - mirrors logging's own behaviour
            self.handleError(record)

    @property
    def invalid_lines(self) -> list[str]:
        """Lines that were not valid JSON.

        Exposed so a test can assert on the *whole* capture. Reading only
        :attr:`records` would silently skip malformed lines, which is exactly
        the regression a "logs are valid JSON" test is meant to catch.
        """
        return [line for line in self.lines if not _is_json(line)]

    @property
    def records(self) -> list[dict]:
        parsed = []
        for line in self.lines:
            if _is_json(line):
                parsed.append(json.loads(line))
        return parsed


def _is_json(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


@pytest.fixture
def json_logs():
    """Capture Anchor's structured log output as parsed dicts."""
    handler = _JsonCaptureHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
