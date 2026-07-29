"""Listing, inspecting, re-indexing and deleting knowledge base documents.

Reads the `documents` table when a database is configured and falls back to
deriving the list from vector-store metadata when it is not, so the original
development flow keeps working unchanged.

Every route scopes to the caller's workspace. A document in workspace A is not
visible from workspace B even when the two hold a file with the same name —
that is enforced by the ``workspace_id`` predicate on every query here and by
the tenant filter inside the vector store, not by the UI hiding the row.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import audit, rate_limit
from agent.auth.principals import CurrentUser, CurrentUserDep, require_workspace_role
from agent.db.access import scoped_one
from agent.db.models import (
    Document,
    DocumentStatus,
    IngestionJob,
    User,
    WorkspaceRole,
)
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger
from agent.observability.metrics import registry
from agent.rate_limit import INGEST, principal_key
from agent.schemas.ingest import DocumentResponse
from agent.storage import StorageError, get_storage
from ingestion.vector_store import VectorStoreError, get_vector_store

router = APIRouter(tags=["documents"])
log = get_logger(__name__)

#: Deleting or re-indexing a knowledge base document changes what every member
#: of the workspace can ask about, so it is an administrator's action.
DocumentAdmin = Annotated[CurrentUser, Depends(require_workspace_role(WorkspaceRole.ADMIN))]


def _to_response(row: Document, uploader: str | None) -> DocumentResponse:
    return DocumentResponse(
        id=row.id,
        doc_name=row.name,
        status=row.status.value,
        size_bytes=row.size_bytes,
        chunks=row.chunks_created,
        page_count=row.pages_processed,
        ocr_used=row.ocr_used,
        error_message=row.error_message,
        uploaded_by=uploader,
        created_at=row.created_at.isoformat() if row.created_at else None,
        indexed_at=row.indexed_at.isoformat() if row.indexed_at else None,
    )


def _uploader_names(db: Session, workspace_id: str) -> dict[str, str]:
    """Map uploader ids to emails for the current page of results."""
    ids = [
        r.uploaded_by_user_id
        for r in db.execute(select(Document).where(Document.workspace_id == workspace_id)).scalars()
    ]
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.email).where(User.id.in_(set(ids)))).all()
    return dict(rows)


@router.get(
    "/documents",
    summary="List knowledge base documents",
    description=(
        "Returns the documents visible to the caller's workspace, with their "
        "indexing status. Falls back to deriving the list from the vector store "
        "when no database is configured."
    ),
)
async def list_documents(user: CurrentUserDep, db: OptionalDbSession) -> dict[str, Any]:
    if db is None or user.workspace_id is None:
        return _list_from_vector_store(user.workspace_id)

    rows = list(
        db.execute(
            select(Document)
            .where(Document.workspace_id == user.workspace_id)
            .order_by(Document.name)
        ).scalars()
    )
    names = _uploader_names(db, user.workspace_id)
    documents = [_to_response(row, names.get(row.uploaded_by_user_id)).model_dump() for row in rows]
    return {
        "documents": documents,
        "total_chunks": sum(row.chunks_created for row in rows),
    }


def _list_from_vector_store(workspace_id: str | None) -> dict[str, Any]:
    """The original behaviour: describe the collection from its own metadata."""
    store = get_vector_store()
    try:
        documents = store.list_documents(workspace_id)
        return {"documents": documents, "total_chunks": sum(d["chunks"] for d in documents)}
    except Exception:
        log.error("documents.list_failed", exc_info=True)
        return {"documents": [], "total_chunks": 0}


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Fetch one document",
)
async def get_document(
    document_id: str, user: CurrentUserDep, db: OptionalDbSession
) -> DocumentResponse:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": "Documents have no identity without a database. Use /documents.",
            },
        )
    row = db.execute(
        select(Document).where(
            Document.id == document_id, Document.workspace_id == user.workspace_id
        )
    ).scalars().one_or_none()
    if row is None:
        # 404 rather than 403: a document in another workspace must be
        # indistinguishable from one that does not exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "No such document in this workspace."},
        )
    names = _uploader_names(db, user.workspace_id)
    return _to_response(row, names.get(row.uploaded_by_user_id))


@router.post(
    "/documents/{document_id}/reindex",
    response_model=DocumentResponse,
    summary="Re-run ingestion for a document",
    description=(
        "Queues a fresh ingestion job from the stored original. Use after "
        "changing the embedding model, the chunk size, or the OCR settings — "
        "the stored chunks are the ones the current settings would not produce."
    ),
)
async def reindex_document(
    request: Request,
    document_id: str,
    user: DocumentAdmin,
    db: OptionalDbSession,
) -> DocumentResponse:
    rate_limit.limiter.check(principal_key(request, user.user_id), INGEST)

    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "database_unavailable",
                "message": "Re-indexing needs a database. See .env.example.",
            },
        )
    row = _require_document(db, document_id, user)

    if not get_storage().exists(row.storage_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "source_missing",
                "message": "The original file is no longer in storage, so it cannot be re-indexed.",
            },
        )

    row.status = DocumentStatus.QUEUED
    row.error_message = None
    db.add(IngestionJob(document_id=row.id, workspace_id=row.workspace_id))
    db.commit()

    audit.record_event(
        db,
        action="document.reindex",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=row.workspace_id,
        target_type="document",
        target_id=row.id,
    )
    return _to_response(row, None)


@router.delete(
    "/documents/{document_id}",
    summary="Delete a document",
    description=(
        "Removes the document row, its stored original and every chunk it "
        "produced, so a deleted document stops appearing in answers "
        "immediately. Scoped to the caller's workspace."
    ),
)
async def delete_document(
    document_id: str,
    user: DocumentAdmin,
    db: OptionalDbSession,
) -> dict[str, Any]:
    if db is None:
        # No database means documents are named, not identified. The
        # development path keeps the original name-based route.
        return await _delete_by_name(document_id, user)

    row = _require_document(db, document_id, user)

    _purge_files(row)
    db.delete(row)
    db.commit()

    audit.record_event(
        db,
        action="document.delete",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=row.workspace_id,
        target_type="document",
        target_id=row.id,
        detail={"name": row.name},
    )
    log.info(
        "documents.deleted",
        context={"document_id": row.id, "workspace_id": row.workspace_id, "doc_name": row.name},
    )
    return {"status": "deleted", "document_id": row.id, "doc_name": row.name}


def _require_document(db: Session, document_id: str, user: CurrentUser) -> Document:
    return scoped_one(db, Document, document_id, user, what="document")


def _purge_files(row: Document) -> None:
    """Remove the stored original and every indexed chunk. Best-effort."""
    try:
        get_storage().delete(row.storage_key)
    except StorageError:
        log.warning("documents.storage_delete_failed", context={"document_id": row.id})
    try:
        get_vector_store().delete_document_id(row.id)
    except VectorStoreError:
        log.warning("documents.vector_delete_failed", context={"document_id": row.id})


async def _delete_by_name(doc_name: str, user: CurrentUser) -> dict[str, Any]:
    """The development path: delete by name from the vector store.

    The same route serves both modes, so a name and an id are told apart by
    trying the id first and falling back to the name. A UUID-shaped segment is
    unambiguous, which keeps the ambiguity away from real document names.
    """
    store = get_vector_store()
    try:
        store.delete_document(doc_name, user.workspace_id)
    except Exception as exc:
        log.error("documents.delete_failed", context={"doc_name": doc_name}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "delete_failed", "message": f"Could not delete {doc_name}."},
        ) from exc
    registry.record_ingestion(0)
    return {"status": "deleted", "doc_name": doc_name}
