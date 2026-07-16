"""The Anchor relational schema.

Everything tenant-owned carries a ``workspace_id`` and every query that touches
one filters on it. The rule is enforced by review and by the isolation tests in
``tests/security/test_tenant_isolation.py`` rather than by a database view,
because a view is easy to forget to use from the one query that matters.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class WorkspaceRole(enum.StrEnum):
    """What a member may do inside one workspace.

    Ordered from most to least capable. ``require_role`` compares against this
    ordering, so a new role slots in without touching the check.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]


_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.OWNER: 40,
    WorkspaceRole.ADMIN: 30,
    WorkspaceRole.MEMBER: 20,
    WorkspaceRole.VIEWER: 10,
}


class DocumentStatus(enum.StrEnum):
    """Lifecycle of an uploaded document.

    ``QUEUED`` is set inside the upload request; the worker walks it through
    ``PROCESSING`` to a terminal ``INDEXED`` or ``FAILED``. The API never blocks
    on the expensive half of that walk.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_column(enum_cls, name: str, default=None):  # noqa: ANN001, ANN202
    """A non-native enum column that stores the lowercase ``.value``.

    Without ``values_callable`` SQLAlchemy persists the member *name*
    (``"OWNER"``) while the API speaks the *value* (``"owner"``). Pinning it
    here keeps the database, the tokens and the JSON consistent.
    """
    return mapped_column(
        Enum(
            enum_cls,
            name=name,
            native_enum=False,
            length=16,
            values_callable=lambda e: [member.value for member in e],
        ),
        nullable=False,
        default=default,
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    #: Argon2id PHC string. Never the plaintext, never reversible.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Set when the user is an admin of the deployment itself, which is a
    #: stronger claim than owning one workspace.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)


class Workspace(Base):
    """A tenant. Every document, conversation, key and metric hangs off one."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    #: Who receives audit and ingestion-failure notices.
    owner_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    #: Per-workspace guardrails. ``None`` means "use the deployment default",
    #: which is what keeps a single global setting meaningful.
    settings: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    """A user's standing inside one workspace."""

    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[WorkspaceRole] = _enum_column(
        WorkspaceRole, "workspace_role", WorkspaceRole.MEMBER
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_memberships_user_workspace"),
        Index("ix_memberships_workspace_id", "workspace_id"),
    )


class WorkspaceInvite(Base):
    """A pending invitation. The invitee redeems the raw token to join."""

    __tablename__ = "workspace_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[WorkspaceRole] = _enum_column(WorkspaceRole, "invite_role", WorkspaceRole.MEMBER)
    #: SHA-256 of the invitation token. The raw value is returned exactly once,
    #: at creation, and is not recoverable from here.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (Index("ix_workspace_invites_workspace_id", "workspace_id"),)


# ---------------------------------------------------------------------------
# Sessions and credentials
# ---------------------------------------------------------------------------
class RefreshToken(Base):
    """A hashed refresh token.

    Rotated on every use: redeeming one marks it consumed and issues its
    successor, so a stolen token is good for at most one exchange and the theft
    shows up as the legitimate client being logged out.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: SHA-256 hex. A database leak does not yield usable tokens.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Coarse client fingerprint, for the "where is this session" list.
    user_agent: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ApiKey(Base):
    """A workspace-scoped programmatic credential.

    Only ``key_prefix`` and the hash are stored. The full secret is shown once
    at creation and is not reconstructable afterwards.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The leading characters, enough for a human to tell two keys apart.
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: Comma-separated scopes, e.g. ``query,documents:read``.
    scopes: Mapped[str] = mapped_column(String(300), nullable=False, default="query")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (Index("ix_api_keys_workspace_id", "workspace_id"),)


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
class Document(Base):
    """An uploaded PDF and its indexing state.

    The object itself lives in object storage; this row is the source of truth
    for ownership and status, and the only place a worker looks to find out
    what to do next.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The human-facing name, unique within a workspace.
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    #: Key in the object store. Kept so a re-index can re-read the original.
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(120), nullable=False, default="application/pdf"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[DocumentStatus] = _enum_column(
        DocumentStatus, "document_status", DocumentStatus.QUEUED
    )
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_using_ocr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_documents_workspace_name"),
        Index("ix_documents_workspace_status", "workspace_id", "status"),
    )


class IngestionJob(Base):
    """One attempt at turning a document into indexed chunks."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = _enum_column(JobStatus, "job_status", JobStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (Index("ix_ingestion_jobs_status_created", "status", "created_at"),)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (Index("ix_conversations_workspace_updated", "workspace_id", "updated_at"),)


class Message(Base):
    """One turn. Assistant turns carry the full telemetry of how they were
    produced, so a support engineer can reconstruct an answer months later."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    model_used: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(40))
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    request_id: Mapped[str | None] = mapped_column(String(64))
    #: JSON array of ``{citation, doc_name, page_number, chunk_id, score}``.
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON array of the tool calls made while producing this turn.
    tool_calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    guardrail_flags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role in ('user','assistant','system')", name="messages_role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
class QueryUsage(Base):
    """One answered question. The analytics dashboard reads only this table —
    no metric in the UI is computed from a counter that dies with the process."""

    __tablename__ = "query_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_used: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    routing_reason: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    guardrail_flags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_type: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_query_usage_workspace_created", "workspace_id", "created_at"),
        Index("ix_query_usage_workspace_model", "workspace_id", "model_used"),
    )


class AuditEvent(Base):
    """A security-relevant action: who did what, to which resource, from where.

    Deliberately not the same table as :class:`QueryUsage`. An audit row is
    about authorisation and is retained on a different clock than a cost record.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    #: Dotted verb, e.g. ``document.delete`` or ``apikey.revoke``.
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Free-form detail. Never the request or response body.
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),)
