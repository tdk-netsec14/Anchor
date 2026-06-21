"""Structured JSON logging with a request-scoped correlation id.

Every log line is a single JSON object so the output can be shipped straight to
a log aggregator without a parsing shim. Request correlation is carried on a
:class:`~contextvars.ContextVar`, which keeps the id correct under both the
threadpool and the asyncio server without threading it through every call.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

# Set once per request by the middleware in agent.main.
request_id_var: ContextVar[str] = ContextVar("anchor_request_id", default="-")


def new_request_id() -> str:
    return str(uuid.uuid4())


def get_request_id() -> str:
    return request_id_var.get()


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one line of JSON."""

    _STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }

        for key, value in record.__dict__.items():
            if key not in self._STANDARD and not key.startswith("_"):
                payload[key] = value

        context = payload.pop("context", None)
        if isinstance(context, dict):
            payload.update(context)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Install the Anchor root handler. Idempotent across repeated calls."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(handler)

    # Uvicorn installs its own handlers; route them through ours so every line
    # stays machine-readable.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a logger that stamps the current request id onto every record."""
    return _BoundLogger(logging.getLogger(name), {})


class _BoundLogger(logging.LoggerAdapter):
    """Logger adapter that re-reads the request id at emit time."""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        context: dict[str, Any] = dict(kwargs.pop("context", None) or {})
        context.setdefault("request_id", request_id_var.get())
        extra = dict(kwargs.get("extra") or {})
        extra["context"] = context
        kwargs["extra"] = extra
        return msg, kwargs
