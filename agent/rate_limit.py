"""Per-principal rate limiting.

A fixed-window counter keyed by principal and route class, held in process
memory. That is an honest backstop against a runaway client and a scripted
abuse, not a distributed quota: behind several API instances each one keeps its
own counters, so the effective ceiling is a multiple of the configured value.
The limit is documented in ``.env.example`` in those terms rather than
described as global, because claiming otherwise would be false.

Tighter classes exist for the expensive routes — a question costs an LLM call,
an upload costs OCR and embedding — because a flat per-minute budget sized for
those would be useless on the cheap ones.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from agent.config import get_settings
from agent.observability.logger import get_logger

log = get_logger(__name__)

#: Route classes and the budget each draws from.
QUERY = "query"
INGEST = "ingest"
DEFAULT = "default"


@dataclass(slots=True)
class _Window:
    count: int = 0
    reset_at: float = 0.0


class RateLimiter:
    """Fixed-window limiter. One window per (key, class)."""

    def __init__(self) -> None:
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = threading.Lock()

    def _budget(self, route_class: str) -> int:
        settings = get_settings()
        if route_class == QUERY:
            return settings.RATE_LIMIT_QUERY_REQUESTS
        if route_class == INGEST:
            return settings.RATE_LIMIT_INGEST_REQUESTS
        return settings.RATE_LIMIT_REQUESTS

    def check(self, key: str, route_class: str = DEFAULT) -> None:
        """Raise 429 when ``key`` has exhausted its budget for this window."""
        settings = get_settings()
        if not settings.RATE_LIMIT_ENABLED:
            return

        limit = self._budget(route_class)
        if limit <= 0:
            return

        now = time.monotonic()
        window_seconds = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
        bucket = (key, route_class)

        with self._lock:
            window = self._windows.get(bucket)
            if window is None or now >= window.reset_at:
                window = _Window(count=0, reset_at=now + window_seconds)
                self._windows[bucket] = window
            if window.count >= limit:
                retry_after = max(1, int(window.reset_at - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limited",
                        "message": "Too many requests. Please slow down and try again shortly.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            window.count += 1
            self._evict_expired(now)

    def _evict_expired(self, now: float) -> None:
        """Drop windows that have rolled over.

        Called on the check path under the lock. Without it the dict grows
        without bound, because every distinct principal that ever calls leaves
        an entry behind.
        """
        if len(self._windows) < 1024:
            return
        stale = [k for k, w in self._windows.items() if now >= w.reset_at]
        for key in stale:
            del self._windows[key]

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()

    def peek(self, key: str, route_class: str = DEFAULT) -> int:
        """Remaining budget. Exposed for the settings page and for tests."""
        with self._lock:
            window = self._windows.get((key, route_class))
            if window is None or time.monotonic() >= window.reset_at:
                return self._budget(route_class)
            return max(0, self._budget(route_class) - window.count)


limiter = RateLimiter()


def principal_key(request: Request, user_id: str) -> str:
    """The rate-limit identity.

    Keyed on the authenticated principal, falling back to the client address
    for unauthenticated routes. Keying on the address alone would let one user
    behind a shared NAT exhaust everyone's budget; keying on the principal
    alone would let an unauthenticated flood run free.
    """
    if user_id:
        return f"user:{user_id}"
    client = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client = forwarded.split(",")[0].strip()
    return f"ip:{client}"


_daily: dict[tuple[str, str], int] = defaultdict(int)
_daily_lock = threading.Lock()


def check_daily_quota(workspace_id: str) -> None:
    """Refuse a workspace that has answered its daily question allowance.

    The counter is in-process, so like the rate limiter it is a backstop. A
    hard global quota needs shared state and belongs in front of the API.
    """
    limit = get_settings().QUOTA_QUERIES_PER_DAY
    if limit <= 0:
        return
    key = (workspace_id, time.strftime("%Y-%m-%d"))
    with _daily_lock:
        used = _daily[key]
        if used >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        f"This workspace has used its allowance of {limit} questions "
                        "for today. It resets at midnight UTC."
                    ),
                },
            )
        _daily[key] = used + 1


def reset_quota() -> None:
    with _daily_lock:
        _daily.clear()
