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
