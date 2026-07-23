"""Audit events.

An audit row answers "who did what to which resource, and was it allowed". It
is deliberately not the same record as :class:`~agent.db.models.QueryUsage`:
one is about authorisation and is kept on a security clock, the other is about
cost and is kept on a billing one.

Two rules hold everywhere this module is called from:

* the detail payload is caller-supplied and must never contain a request body,
  an answer, a password or a token;
* a write failure is logged but never raised. Losing an audit row is bad; failing
  a user's login because the audit insert hit a constraint is worse.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from agent.db.models import AuditEvent
from agent.observability.logger import get_logger, get_request_id

log = get_logger(__name__)

#: Keys that must never reach the audit detail, whatever a caller passes.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {"password", "token", "secret", "authorization", "api_key", "content", "answer", "body"}
)


def record_event(
    db: Session,
    *,
    action: str,
    actor_email: str = "",
    user_id: str | None = None,
    workspace_id: str | None = None,
    target_type: str = "",
    target_id: str = "",
    outcome: str = "success",
    ip_address: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit event. Never raises."""
    try:
        db.add(
            AuditEvent(
                action=action,
                actor_email=actor_email[:320],
                user_id=user_id,
                workspace_id=workspace_id,
                target_type=target_type[:40],
                target_id=str(target_id)[:64],
                outcome=outcome,
                ip_address=ip_address[:64],
                request_id=get_request_id() or "",
                detail_json=json.dumps(_sanitise(detail or {})),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        log.error("audit.write_failed", context={"action": action}, exc_info=True)


def _sanitise(detail: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that looks like a credential, and coerce to JSON-safe types.

    This is a backstop, not the primary control: a caller that puts a secret in
    a differently-named key still gets it stored. Callers are expected to pass
    identifiers and counts.
    """
    safe: dict[str, Any] = {}
    for key, value in detail.items():
        if key.lower() in _FORBIDDEN_DETAIL_KEYS:
            safe[key] = "[redacted]"
        elif isinstance(value, str | int | float | bool) or value is None:
            safe[key] = value
        else:
            safe[key] = str(value)[:200]
    return safe
