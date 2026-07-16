"""Persistence for Anchor: engine, schema and session access.

Import the models from here rather than from :mod:`agent.db.models` directly so
that a `Base.metadata` reflection always sees every table, whatever the import
order happened to be.
"""

from agent.db.base import (
    Base,
    get_engine,
    get_session_factory,
    reset_engine,
    session_scope,
)
from agent.db.models import (
    ApiKey,
    AuditEvent,
    Conversation,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Membership,
    Message,
    PasswordResetToken,
    QueryUsage,
    RefreshToken,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceRole,
)

__all__ = [
    "ApiKey",
    "AuditEvent",
    "Base",
    "Conversation",
    "Document",
    "DocumentStatus",
    "IngestionJob",
    "JobStatus",
    "Membership",
    "Message",
    "PasswordResetToken",
    "QueryUsage",
    "RefreshToken",
    "User",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceRole",
    "get_engine",
    "get_session_factory",
    "reset_engine",
    "session_scope",
]
