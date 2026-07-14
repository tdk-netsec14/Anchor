"""``POST /ingest`` — upload a PDF into the knowledge base.

Admin only. Writing to the shared knowledge base is the one operation a normal
support user must never perform, so the route is gated by ``require_role``
rather than relying on the client to hide the button.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from agent.auth import require_role
from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.observability.metrics import registry
from agent.schemas.ingest import IngestResponse
from ingestion.ocr import OcrUnavailableError, PdfExtractionError
from ingestion.pipeline import (
    EmptyDocumentError,
    IngestionError,
    ingest_document,
)
from ingestion.vector_store import VectorStoreError

# `Depends(require_role("admin"))` must receive the *callable*, not the
# Annotated alias - handing FastAPI an Annotated type makes it introspect the
# alias itself and invent `args`/`kwargs` query parameters.
router = APIRouter(tags=["ingestion"], dependencies=[Depends(require_role("admin"))])
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


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Upload and index a PDF (admin only)",
    description=(
        "Runs the uploaded PDF through text extraction (OCR where the page has "
        "no text layer), chunking, local embedding and ChromaDB storage.\n\n"
        "Requires the `admin` role. Re-uploading a document with the same name "
        "replaces its previous chunks."
    ),
)
async def ingest_pdf(
    file: Annotated[UploadFile, File(description="PDF file to index.")],
    doc_name: Annotated[
        str | None,
        Form(description="Optional override for the indexed document name."),
    ] = None,
) -> IngestResponse:
    settings = get_settings()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    name = _safe_doc_name(file, doc_name)
    data = await _read_limited(file, max_bytes)

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
