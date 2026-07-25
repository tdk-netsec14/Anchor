"""Ingestion orchestration: bytes in, searchable chunks in the vector store.

This is the single implementation used by both entry points — the batch worker
(``ingestion/main.py``) and the Agent API's ``POST /ingest`` — so the two paths
cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.config import get_settings
from agent.observability.logger import get_logger
from ingestion.chunker import Chunk, chunk_pages
from ingestion.embedder import get_embedder
from ingestion.ocr import Page, extract_pages
from ingestion.vector_store import VectorStoreError, get_vector_store

log = get_logger(__name__)


class IngestionError(RuntimeError):
    """Base class for recoverable ingestion failures."""


class EmptyDocumentError(IngestionError):
    """No text could be recovered from the document by any method."""


@dataclass(slots=True)
class IngestResult:
    doc_name: str
    status: str
    chunks_created: int
    pages_processed: int = 0
    pages_using_ocr: int = 0
    total_tokens: int = 0
    replaced_existing: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "doc_name": self.doc_name,
            "chunks_created": self.chunks_created,
            "status": self.status,
            "pages_processed": self.pages_processed,
            "pages_using_ocr": self.pages_using_ocr,
            "total_tokens": self.total_tokens,
            "replaced_existing": self.replaced_existing,
            "warnings": self.warnings,
        }


def build_chunks(
    pages: list[Page],
    doc_name: str,
    *,
    use_model_tokenizer: bool = True,
    workspace_id: str | None = None,
    document_id: str | None = None,
) -> list[Chunk]:
    """Chunk extracted pages using the embedding model's own tokenizer.

    The configured ``CHUNK_SIZE_TOKENS`` is clamped to the embedding model's
    maximum sequence length. The default of 500 is a reasonable retrieval window,
    but all-MiniLM-L6-v2 only encodes 256 tokens — a larger chunk would have its
    tail silently dropped at encode time, so the second half would never be
    searchable. Clamping keeps the setting honest about what is actually stored.
    """
    settings = get_settings()
    span_fn = None
    chunk_size = settings.CHUNK_SIZE_TOKENS

    if use_model_tokenizer:
        embedder = get_embedder()
        try:
            # Load first: `char_spans` and `max_sequence_length` are properties
            # that read the loaded model, so referencing them before the model
            # exists would silently report "unknown".
            embedder.load()
            span_fn = embedder.char_spans
            model_limit = embedder.max_sequence_length
            if model_limit and chunk_size > model_limit:
                log.warning(
                    "ingestion.chunk_size_clamped",
                    context={
                        "configured": chunk_size,
                        "model_max": model_limit,
                        "model": embedder.model_name,
                    },
                )
                chunk_size = model_limit
        except Exception as exc:  # model unavailable -> fall back to word spans
            log.warning(
                "ingestion.tokenizer_fallback",
                context={"reason": type(exc).__name__},
            )

    overlap = min(settings.CHUNK_OVERLAP_TOKENS, max(0, chunk_size // 2))
    return chunk_pages(
        pages,
        doc_name,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        span_fn=span_fn,
        workspace_id=workspace_id,
        document_id=document_id,
    )


def ingest_document(
    data: bytes,
    doc_name: str,
    *,
    replace_existing: bool = True,
    workspace_id: str | None = None,
    document_id: str | None = None,
) -> IngestResult:
    """Run one document through extract -> chunk -> embed -> store.

    ``workspace_id`` and ``document_id`` are stamped onto every chunk. They
    are what makes retrieval and deletion tenant-scoped, so a deployment with a
    database always supplies them; the API refuses to ingest without one.
    """
    settings = get_settings()
    store = get_vector_store()
    # Open the native Chroma extension before any embedding model is loaded;
    # see VectorStore.initialize for why the order matters.
    store.initialize()
    embedder = get_embedder()

    if not data:
        raise IngestionError("The uploaded file is empty.")

    pages = extract_pages(
        data,
        ocr_enabled=settings.OCR_ENABLED,
        min_chars_per_page=settings.OCR_MIN_CHARS_PER_PAGE,
        ocr_language=settings.OCR_LANGUAGE,
    )
    if not pages:
        raise EmptyDocumentError("The document contains no pages.")

    chunks = build_chunks(pages, doc_name, workspace_id=workspace_id, document_id=document_id)
    if not chunks:
        raise EmptyDocumentError(
            "No text could be extracted from this document. If it is a scan, "
            "ensure Tesseract OCR is installed and OCR_ENABLED=true."
        )

    replaced = False
    if replace_existing:
        # Chunk ids are deterministic, so a plain upsert would overwrite the same
        # ids — but a document that shrank would leave stale trailing chunks.
        # Whether we are *replacing* is about this document only, so it has to
        # be counted before the delete: after it, the collection total says
        # nothing about whether this file was already indexed.
        try:
            replaced = store.count_documents(doc_name, workspace_id) > 0
        except VectorStoreError as exc:
            log.warning("ingestion.count_failed", context={"reason": type(exc).__name__})
        try:
            if document_id:
                store.delete_document_id(document_id)
            else:
                store.delete_document(doc_name, workspace_id)
        except VectorStoreError as exc:  # deleting from an empty store is not an error
            log.warning("ingestion.delete_skipped", context={"reason": type(exc).__name__})

    embeddings = embedder.embed_documents([c.text for c in chunks])
    written = store.upsert_chunks(chunks, embeddings)

    result = IngestResult(
        doc_name=doc_name,
        status="success",
        chunks_created=written,
        pages_processed=len(pages),
        pages_using_ocr=sum(1 for p in pages if p.ocr_used),
        total_tokens=sum(c.token_count for c in chunks),
        replaced_existing=replaced,
    )
    log.info(
        "ingestion.completed",
        context={
            "doc_name": doc_name,
            "workspace_id": workspace_id,
            "chunks_created": written,
            "pages": len(pages),
            "ocr_pages": result.pages_using_ocr,
        },
    )
    return result


def ingest_file(path: str | Path, **kwargs) -> IngestResult:
    file_path = Path(path)
    return ingest_document(file_path.read_bytes(), file_path.name, **kwargs)
