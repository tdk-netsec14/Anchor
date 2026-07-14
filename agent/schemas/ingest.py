"""Request/response models for document ingestion."""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    """Result of pushing one PDF through the pipeline."""

    doc_name: str = Field(description="Name the document was indexed under.")
    chunks_created: int = Field(description="Number of embedded chunks written.")
    status: str = Field(description="'success' when the document is searchable.")
    pages_processed: int = 0
    pages_using_ocr: int = 0
    total_tokens: int = 0
    replaced_existing: bool = Field(
        default=False, description="True when a previous version was overwritten."
    )
    warnings: list[str] = Field(default_factory=list)
