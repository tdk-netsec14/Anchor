"""``POST /ingest`` — upload a PDF into the knowledge base.

Two modes, chosen by whether a database is configured:

**With a database** (every real deployment). The request validates the upload,
writes the bytes to object storage, creates a `documents` row in state `QUEUED`
and an `ingestion_jobs` row, and returns. Extraction, OCR, embedding and
indexing happen in the background worker. The caller's connection is not held
open for the tens of seconds that work takes.

**Without one** (the original development path). The request ingests
synchronously and returns the counts directly. There is no database, so there
are no tenants, no ownership and no job to hand work to.

Both write to the same vector store with the same chunking, and both are gated
by role: writing to the knowledge base is the one thing a read-only member must
never be able to do.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from agent import audit, rate_limit
from agent.auth.principals import CurrentUser, require_active_workspace, require_role
from agent.config import get_settings
from agent.db.models import Document, DocumentStatus, IngestionJob
from agent.db.session import OptionalDbSession
from agent.observability.logger import get_logger
from agent.observability.metrics import registry
from agent.rate_limit import INGEST, principal_key
from agent.schemas.ingest import IngestResponse
from agent.storage import StorageError, build_key, get_storage
from ingestion.ocr import OcrUnavailableError, PdfExtractionError
from ingestion.pipeline import EmptyDocumentError, IngestionError, ingest_document
from ingestion.vector_store import VectorStoreError, get_vector_store

router = APIRouter(tags=["ingestion"])
log = get_logger(__name__)

#: Every PDF starts with this; checked so a mislabelled upload produces a clear
#: message instead of an opaque parser error.
PDF_MAGIC = b"%PDF"


def _safe_doc_name(upload: UploadFile, override: str | None) -> str:
    """Pick the name to index under, refusing anything that escapes data/."""
    candidate = (override or upload.filename or "document.pdf").strip()
    candidate = candidate.replace("\\", "/").split("/")[-1]
    if not candidate.lower().endswith(".pdf"):
        candidate = f"{candidate}.pdf"
    # Strip separators and control characters; the name becomes a Chroma
    # metadata value and part of every chunk id.
    cleaned = "".join(c for c in candidate if c.isprintable() and c not in ':*?"<>|')
    cleaned = cleaned.strip(". ")
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_filename", "message": "No usable file name supplied."},
        )
    return cleaned[:180]


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in blocks, aborting as soon as it exceeds the limit.

    Reading the whole body first and then checking its length would let a
    single request exhaust container memory.
    """
    buffer = bytearray()
    while True:
        block = await upload.read(1024 * 1024)
        if not block:
            break
        buffer.extend(block)
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "error": "file_too_large",
                    "message": f"The upload exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                },
            )
    return bytes(buffer)


async def _validate_upload(upload: UploadFile, override: str | None) -> tuple[str, bytes]:
    """Check name, size and magic bytes. Shared by both modes."""
    settings = get_settings()
    name = _safe_doc_name(upload, override)
    data = await _read_limited(upload, settings.MAX_UPLOAD_MB * 1024 * 1024)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "The uploaded file is empty."},
        )
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "not_a_pdf",
                "message": "The uploaded file is not a PDF (missing %PDF header).",
            },
        )
    return name, data


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Upload and index a PDF",
    description=(
        "Stores the PDF and indexes it: text extraction (OCR where the page has "
        "no text layer), chunking, local embedding and vector storage.\n\n"
        "With a database configured the response returns as soon as the file is "
        'stored, with `status="queued"`; a background worker does the rest and '
        "the document moves through `processing` to `indexed` or `failed`. "
        "Without one, the request ingests synchronously.\n\n"
        "Requires the `admin` role, or OWNER/ADMIN in the active workspace. "
        "Re-uploading a name that already exists in this workspace replaces the "
        "previous version."
    ),
)
async def ingest_pdf(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_role("admin"))],
    db: OptionalDbSession,
    file: Annotated[UploadFile, File(description="PDF file to index.")],
    doc_name: Annotated[
        str | None,
        Form(description="Optional override for the indexed document name."),
    ] = None,
) -> IngestResponse:
    rate_limit.limiter.check(principal_key(request, user.user_id), INGEST)
    name, data = await _validate_upload(file, doc_name)

    if db is None:
        # No database means no tenants, so there is no workspace to require and
        # nothing to own the document. The role check above is the only gate.
        return await _ingest_synchronously(data, name)

    # With a database the document is a tenant-owned row, so the workspace must
    # come from the credential. A demo token names none and is refused here.
    return _queue_ingestion(db, user, require_active_workspace(user), name, data)


async def _ingest_synchronously(data: bytes, name: str) -> IngestResponse:
    """The original path: do the whole pipeline inside the request."""
    try:
        # Parsing, OCR, embedding and the Chroma write are all synchronous and
        # can take seconds. Running them on the event loop would stall every
        # other in-flight request for the duration, so they go to a worker.
        result = await run_in_threadpool(ingest_document, data, name)
    except PdfExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_pdf", "message": str(exc)},
        ) from exc
    except OcrUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "ocr_unavailable", "message": str(exc)},
        ) from exc
    except EmptyDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "empty_document", "message": str(exc)},
        ) from exc
    except VectorStoreError as exc:
        log.error("ingest.vector_store_error", context={"doc_name": name}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "vector_store_unavailable",
                "message": "The knowledge base is not reachable. Try again shortly.",
            },
        ) from exc
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "ingestion_failed", "message": str(exc)},
        ) from exc

    registry.record_ingestion(result.chunks_created)
    return IngestResponse(**result.to_dict())  # type: ignore[arg-type]


def _queue_ingestion(
    db,  # noqa: ANN001
    user: CurrentUser,
    workspace_id: str,
    name: str,
    data: bytes,
) -> IngestResponse:
    """Store the file and queue the work. Returns without indexing anything."""

    # Re-upload replaces. The old row is removed here rather than updated so
    # the new document gets a fresh id, a fresh storage key, and — crucially —
    # chunk ids that cannot collide with the previous version's.
    previous = db.execute(
        select(Document).where(Document.workspace_id == workspace_id, Document.name == name)
    ).scalars().one_or_none()
    replaced = previous is not None
    if previous is not None:
        _purge_document_files(previous)
        db.delete(previous)
        db.flush()

    document = Document(
        workspace_id=workspace_id,
        uploaded_by_user_id=user.user_id,
        name=name,
        # Filled in below, once the id exists to key it by.
        storage_key="pending",
        content_type="application/pdf",
        size_bytes=len(data),
        status=DocumentStatus.QUEUED,
    )
    db.add(document)
    db.flush()

    document.storage_key = build_key(workspace_id, document.id, name)
    try:
        get_storage().put(document.storage_key, data, "application/pdf")
    except StorageError as exc:
        db.rollback()
        log.error("ingest.storage_failed", context={"doc_name": name}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "storage_unavailable",
                "message": "The document could not be stored. Try again shortly.",
            },
        ) from exc

    db.add(IngestionJob(document_id=document.id, workspace_id=workspace_id))
    try:
        db.commit()
    except IntegrityError as exc:
        # Two uploads of the same name raced. One wins; the other is told to
        # retry, which is the honest answer.
        db.rollback()
        log.warning(
            "ingest.name_conflict", context={"doc_name": name, "workspace_id": workspace_id}
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "document_exists",
                "message": f"A document named '{name}' already exists in this workspace.",
            },
        ) from exc

    audit.record_event(
        db,
        action="document.upload",
        actor_email=user.email or "",
        user_id=user.user_id,
        workspace_id=workspace_id,
        target_type="document",
        target_id=document.id,
        detail={"name": name, "size_bytes": len(data), "replaced": replaced},
    )
    log.info(
        "ingest.queued",
        context={
            "document_id": document.id,
            "workspace_id": workspace_id,
            "doc_name": name,
            "size_bytes": len(data),
            "replaced": replaced,
        },
    )

    return IngestResponse(
        doc_name=name,
        chunks_created=0,
        status="queued",
        document_id=document.id,
        document_status=DocumentStatus.QUEUED,
        replaced_existing=replaced,
    )


def _purge_document_files(document) -> None:  # noqa: ANN001
    """Remove a document's stored bytes and its indexed chunks.

    Both are best-effort. A failure here is logged and the caller still
    removes the row, because a database row pointing at a missing object is
    worse than an orphaned object nobody references.
    """
    try:
        get_storage().delete(document.storage_key)
    except StorageError:
        log.warning(
            "documents.storage_delete_failed",
            context={"document_id": document.id},
            exc_info=True,
        )
    try:
        get_vector_store().delete_document_id(document.id)
    except VectorStoreError:
        log.warning(
            "documents.vector_delete_failed",
            context={"document_id": document.id},
            exc_info=True,
        )
