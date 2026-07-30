"""Persistent conversations.

A conversation is a thread of messages inside one workspace. Every turn is
stored with the telemetry of how it was produced — model, provider, latency,
tokens, cost, sources, tool calls, guardrail flags and the request id — so an
answer can be reconstructed and audited long after it was given.

Access is scoped two ways: a workspace member sees the workspace's
conversations, and a user sees their own. The first is what a shared support
queue needs; the second is what keeps one member's drafts out of a colleague's
sidebar unless the workspace is genuinely collaborative.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent import audit
from agent.auth.principals import CurrentUser, CurrentUserDep
from agent.db.access import scoped_one
from agent.db.models import Conversation, Message, WorkspaceRole
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger
from agent.schemas.query import SourceDetail, ToolCallRecord

router = APIRouter(prefix="/conversations", tags=["conversations"])
log = get_logger(__name__)

MAX_TITLE_CHARS = 200

#: An administrator may tidy up any conversation in the workspace; a plain
#: member may only manage their own.
_ADMIN = WorkspaceRole.ADMIN


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=MAX_TITLE_CHARS)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    model_used: str | None = None
    provider: str | None = None
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    request_id: str | None = None
    sources: list[SourceDetail] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    guardrail_flags: list[str] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _loads(value: str) -> list[Any]:
    """Parse a JSON column, tolerating one written by an older build."""
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _to_message(row: Message) -> MessageResponse:
    return MessageResponse(
        id=row.id,
        role=row.role,
        content=row.content,
        created_at=row.created_at.isoformat(),
        model_used=row.model_used,
        provider=row.provider,
        latency_ms=row.latency_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        cost_usd=row.cost_usd,
        request_id=row.request_id,
        sources=[SourceDetail(**s) for s in _loads(row.sources_json)],
        tool_calls=[ToolCallRecord(**t) for t in _loads(row.tool_calls_json)],
        guardrail_flags=_loads(row.guardrail_flags_json),
    )


def _require_db(db: Session | None) -> Session:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "database_unavailable",
                "message": "Conversations need a database. Set DATABASE_URL and run the migrations.",
            },
        )
    return db


def _load_conversation(db: Session, conversation_id: str, user: CurrentUser) -> Conversation:
    return scoped_one(db, Conversation, conversation_id, user, what="conversation")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ConversationSummary], summary="List conversations")
async def list_conversations(
    user: CurrentUserDep,
    db: OptionalDbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    include_archived: bool = False,
) -> list[ConversationSummary]:
    session = _require_db(db)
    assert user.workspace_id is not None or not user.is_demo

    counts = (
        select(Message.conversation_id, func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.workspace_id == user.workspace_id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    stmt = (
        select(Conversation, func.coalesce(counts.c[1], 0))
        .outerjoin(counts, counts.c[0] == Conversation.id)
        .where(Conversation.workspace_id == user.workspace_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    if not include_archived:
        stmt = stmt.where(Conversation.archived_at.is_(None))

    return [
        ConversationSummary(
            id=row.id,
            title=row.title,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
            message_count=int(count),
        )
        for row, count in session.execute(stmt).all()
    ]


@router.post(
    "",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
async def create_conversation(
    payload: ConversationCreate, user: CurrentUserDep, db: OptionalDbSession
) -> ConversationSummary:
    session = _require_db(db)
    row = Conversation(
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        title=(payload.title or "New conversation")[:MAX_TITLE_CHARS],
    )
    session.add(row)
    session.commit()
    return ConversationSummary(
        id=row.id,
        title=row.title,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        message_count=0,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail, summary="Read a conversation")
async def get_conversation(
    conversation_id: str, user: CurrentUserDep, db: OptionalDbSession
) -> ConversationDetail:
    session = _require_db(db)
    row = _load_conversation(session, conversation_id, user)
    messages = list(
        session.execute(
            select(Message).where(Message.conversation_id == row.id).order_by(Message.created_at)
        ).scalars()
    )
    return ConversationDetail(
        id=row.id,
        title=row.title,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        message_count=len(messages),
        messages=[_to_message(m) for m in messages],
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary, summary="Rename")
async def rename_conversation(
    conversation_id: str,
    payload: ConversationRename,
    user: CurrentUserDep,
    db: OptionalDbSession,
) -> ConversationSummary:
    session = _require_db(db)
    row = _load_conversation(session, conversation_id, user)
    # The author renames their own; so does an administrator, who may be
    # tidying up after someone else left.
    if row.user_id != user.user_id and not user.has_role(_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "not_the_author",
                "message": "Only the author or a workspace administrator can rename this.",
            },
        )
    row.title = payload.title[:MAX_TITLE_CHARS]
    row.updated_at = datetime.now(UTC)
    session.commit()
    count = session.execute(
        select(func.count(Message.id)).where(Message.conversation_id == row.id)
    ).scalar_one()
    return ConversationSummary(
        id=row.id,
        title=row.title,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        message_count=int(count),
    )


@router.delete("/{conversation_id}", summary="Delete a conversation")
async def delete_conversation(
    conversation_id: str, user: CurrentUserDep, db: OptionalDbSession
) -> dict[str, str]:
    session = _require_db(db)
    row = _load_conversation(session, conversation_id, user)
    if row.user_id != user.user_id and not user.has_role(_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "not_the_author",
                "message": "Only the author or a workspace administrator can delete this.",
            },
        )
    # Messages cascade from the relationship, not from a database-level ON
    # DELETE CASCADE, so they go with it in one transaction.
    session.delete(row)
    session.commit()
    audit.record_event(
        session,
        action="conversation.delete",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        target_type="conversation",
        target_id=conversation_id,
    )
    return {"status": "deleted", "conversation_id": conversation_id}
