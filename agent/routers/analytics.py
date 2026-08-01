"""Workspace analytics.

Every number here is a query against the `query_usage` and `documents` tables.
Nothing is derived from the in-process metric registry, which resets on restart
and is shared across tenants — a dashboard built from it would be both
ephemeral and wrong the moment a second workspace existed.

When no database is configured the endpoint returns 503 rather than zeros. A
dashboard showing "0 requests" when the truth is "we are not recording any" is
worse than an error, because it reads as a fact about usage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from agent.auth.principals import CurrentUserDep
from agent.db.models import AuditEvent, Document, DocumentStatus, QueryUsage, User
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger

router = APIRouter(prefix="/analytics", tags=["analytics"])
log = get_logger(__name__)

def _require_db(db: Session | None) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "database_unavailable",
                "message": "Analytics are computed from recorded usage, which needs a database. "
                "Set DATABASE_URL and run the migrations.",
            },
        )
    return db


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@router.get(
    "/overview",
    summary="Headline metrics for the workspace",
    description=(
        "Totals, latency, token spend, model mix, tool usage, guardrail flags "
        "and knowledge base state, all computed from the workspace's own "
        "recorded activity."
    ),
)
async def overview(
    user: CurrentUserDep,
    db: OptionalDbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    session = _require_db(db)
    workspace_id = user.workspace_id
    window = _since(days)

    totals = session.execute(
        select(
            func.count(QueryUsage.id),
            func.coalesce(func.sum(QueryUsage.prompt_tokens), 0),
            func.coalesce(func.sum(QueryUsage.completion_tokens), 0),
            func.coalesce(func.sum(QueryUsage.cost_usd), 0.0),
            func.coalesce(func.avg(QueryUsage.latency_ms), 0.0),
            func.coalesce(func.max(QueryUsage.latency_ms), 0.0),
            func.coalesce(func.sum(case((QueryUsage.error_type.is_not(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case((QueryUsage.used_fallback.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(QueryUsage.sources_count), 0),
        ).where(QueryUsage.workspace_id == workspace_id, QueryUsage.created_at >= window)
    ).one()
    (
        total,
        prompt_tokens,
        completion_tokens,
        cost,
        avg_latency,
        max_latency,
        errors,
        fallbacks,
        sources,
    ) = totals

    scope = (QueryUsage.workspace_id == workspace_id, QueryUsage.created_at >= window)
    by_model = _tally(
        session,
        select(QueryUsage.model_used, func.count(QueryUsage.id))
        .where(*scope)
        .group_by(QueryUsage.model_used),
    )
    by_provider = _tally(
        session,
        select(QueryUsage.provider, func.count(QueryUsage.id))
        .where(*scope)
        .group_by(QueryUsage.provider),
    )
    by_user = _tally(
        session,
        select(User.email, func.count(QueryUsage.id))
        .join(User, User.id == QueryUsage.user_id)
        .where(*scope)
        .group_by(User.email),
    )

    tools = session.execute(
        select(QueryUsage.tool_calls_json).where(
            QueryUsage.workspace_id == workspace_id, QueryUsage.created_at >= window
        )
    ).scalars()
    tool_counts: dict[str, int] = {}
    for raw in tools:
        for name in _as_list(raw):
            tool_counts[name] = tool_counts.get(name, 0) + 1

    flags = session.execute(
        select(QueryUsage.guardrail_flags_json).where(
            QueryUsage.workspace_id == workspace_id, QueryUsage.created_at >= window
        )
    ).scalars()
    flag_counts: dict[str, int] = {}
    for raw in flags:
        for name in _as_list(raw):
            flag_counts[name] = flag_counts.get(name, 0) + 1

    documents = session.execute(
        select(Document.status, func.count(Document.id), func.coalesce(func.sum(Document.chunks_created), 0)).where(
            Document.workspace_id == workspace_id
        ).group_by(Document.status)
    ).all()
    doc_status = {row[0].value: int(row[1]) for row in documents}
    doc_chunks = sum(int(row[2]) for row in documents)

    return {
        "window_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "requests": {
            "total": int(total),
            "errors": int(errors),
            "fallbacks": int(fallbacks),
            "success_rate": round(1 - (int(errors) / int(total)), 4) if total else 1.0,
        },
        "latency_ms": {
            "average": round(float(avg_latency), 2),
            "max": round(float(max_latency), 2),
        },
        "tokens": {
            "prompt": int(prompt_tokens),
            "completion": int(completion_tokens),
            "total": int(prompt_tokens) + int(completion_tokens),
        },
        "cost": {
            "total_usd": round(float(cost), 6),
            "average_usd_per_request": round(float(cost) / int(total), 6) if total else 0.0,
        },
        "retrieval": {"citations_returned": int(sources)},
        "by_model": by_model,
        "by_provider": by_provider,
        "by_user": by_user,
        "tool_calls": {"total": sum(tool_counts.values()), "by_name": tool_counts},
        "guardrail_flags": flag_counts,
        "documents": {
            "total": sum(doc_status.values()),
            "by_status": doc_status,
            "chunks": doc_chunks,
            "failed": doc_status.get(DocumentStatus.FAILED.value, 0),
        },
    }


def _tally(session: Session, statement) -> dict[str, int]:  # noqa: ANN001 - a Select
    """Turn a ``(label, count)`` query into a dict.

    Empty labels are dropped, so a provider that failed to record its name does
    not become an entry in a chart legend.
    """
    return {str(label): int(count) for label, count in session.execute(statement).all() if label}


def _as_list(raw: str | None) -> list[str]:
    import json

    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


@router.get("/timeseries", summary="Requests and spend over time")
async def timeseries(
    user: CurrentUserDep,
    db: OptionalDbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """Daily counts, latency, tokens and cost.

    Bucketing happens in Python rather than SQL so the same code produces the
    same series on SQLite and PostgreSQL — the dashboard and its tests should
    not depend on which database is underneath.
    """
    session = _require_db(db)
    window = _since(days)
    rows = session.execute(
        select(
            QueryUsage.created_at,
            QueryUsage.latency_ms,
            QueryUsage.prompt_tokens,
            QueryUsage.completion_tokens,
            QueryUsage.cost_usd,
            QueryUsage.error_type,
        ).where(
            QueryUsage.workspace_id == user.workspace_id, QueryUsage.created_at >= window
        )
    ).all()

    buckets: dict[str, dict[str, Any]] = {}
    for created_at, latency, prompt, completion, cost, error_type in rows:
        key = created_at.astimezone(UTC).date().isoformat()
        bucket = buckets.setdefault(
            key,
            {"date": key, "requests": 0, "errors": 0, "tokens": 0, "cost_usd": 0.0, "_latency": 0.0},
        )
        bucket["requests"] += 1
        bucket["errors"] += 1 if error_type else 0
        bucket["tokens"] += int(prompt or 0) + int(completion or 0)
        bucket["cost_usd"] += float(cost or 0.0)
        bucket["_latency"] += float(latency or 0.0)

    series = []
    for key in sorted(buckets):
        bucket = buckets[key]
        count = bucket["requests"]
        bucket["average_latency_ms"] = round(bucket.pop("_latency") / count, 2) if count else 0.0
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)
        series.append(bucket)

    return {"window_days": days, "points": series}


@router.get(
    "/activity",
    summary="Recent workspace activity",
    description=(
        "The most recent questions asked in this workspace, across all its "
        "members. Query text is truncated and never returned in full."
    ),
)
async def recent_activity(
    user: CurrentUserDep,
    db: OptionalDbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[dict[str, Any]]:
    session = _require_db(db)
    rows = session.execute(
        select(QueryUsage, User.email)
        .join(User, User.id == QueryUsage.user_id)
        .where(QueryUsage.workspace_id == user.workspace_id)
        .order_by(QueryUsage.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "request_id": usage.request_id,
            "created_at": usage.created_at.isoformat(),
            "user_email": email,
            "model_used": usage.model_used,
            "provider": usage.provider,
            "latency_ms": usage.latency_ms,
            "tokens_used": usage.prompt_tokens + usage.completion_tokens,
            "cost_usd": usage.cost_usd,
            "sources_count": usage.sources_count,
            "tool_calls": _as_list(usage.tool_calls_json),
            "guardrail_flags": _as_list(usage.guardrail_flags_json),
            "error_type": usage.error_type,
        }
        for usage, email in rows
    ]


@router.get("/audit", summary="Audit trail", description="Security-relevant events for this workspace.")
async def audit_trail(
    user: CurrentUserDep,
    db: OptionalDbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    session = _require_db(db)
    rows = session.execute(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == user.workspace_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "action": row.action,
            "actor_email": row.actor_email,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "outcome": row.outcome,
            "request_id": row.request_id,
            "ip_address": row.ip_address,
        }
        for row in rows
    ]
