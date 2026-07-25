"""Request/response models for document ingestion and management."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.db.models import DocumentStatus


class IngestResponse(BaseModel):
    """Result of pushing one PDF through the pipeline.

    Two shapes share this model because two modes exist. With a database
    configured, the upload returns immediately with ``status="queued"`` and the
    counts stay at zero — the worker fills them in later. Without one, the
    request ingests synchronously and the counts are real.
    """

    doc_name: str = Field(description="Name the document was indexed under.")
    chunks_created: int = Field(description="Number of embedded chunks written.")
    status: str = Field(description="'queued' when asynchronous, 'success' when indexed.")
    pages_processed: int = 0
    pages_using_ocr: int = 0
    total_tokens: int = 0
    replaced_existing: bool = Field(
        default=False, description="True when a previous version was overwritten."
    )
    warnings: list[str] = Field(default_factory=list)
    #: Present only on the asynchronous path.
    document_id: str | None = None
    document_status: DocumentStatus | None = None


class DocumentResponse(BaseModel):
    """One row of the knowledge base, as the UI sees it."""

    id: str | None = Field(
        default=None,
        description="Null on the development path, where documents live only in the vector store.",
    )
    doc_name: str
    status: str = Field(description="queued, processing, indexed or failed.")
    size_bytes: int = 0
    chunks: int = 0
    page_count: int = 0
    ocr_used: bool = False
    error_message: str | None = None
    uploaded_by: str | None = None
    created_at: str | None = None
    indexed_at: str | None = None
