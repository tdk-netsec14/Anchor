"""Real ingestion pipeline: PDF -> text/OCR -> chunk -> embed -> ChromaDB.

These load the actual embedding model and touch a real vector store, so they are
marked `slow` and `integration`. Point them at a throwaway store with
``CHROMA_PERSIST_DIR``; the bundled conftest already isolates them.

    pytest tests/integration/test_ingestion.py
    pytest tests/integration/test_ingestion.py -m "not slow"   # skip
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from ingestion.ocr import (
    PdfExtractionError,
    extract_pages,
    ocr_available,
)
from ingestion.pipeline import IngestionError, ingest_document

pytestmark = [pytest.mark.integration, pytest.mark.slow]

SAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "documents"


def sample(name: str) -> bytes:
    path = SAMPLE_DIR / name
    if not path.exists():
        pytest.skip(f"sample document missing: {name} (run scripts/make_sample_docs.py)")
    return path.read_bytes()


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
class TestExtraction:
    def test_native_text_layer_is_used_without_ocr(self) -> None:
        pages = extract_pages(sample("hr_leave_policy.pdf"), ocr_enabled=True, min_chars_per_page=40)
        assert pages
        assert all(not p.ocr_used for p in pages), "a text-layer PDF should not be OCRed"
        assert "25 days" in " ".join(p.text for p in pages)

    def test_page_numbers_are_preserved(self) -> None:
        pages = extract_pages(sample("it_support_policy.pdf"), ocr_enabled=True)
        assert [p.page_number for p in pages] == list(range(1, len(pages) + 1))
        assert len(pages) >= 2

    def test_ocr_is_used_for_a_scanned_document(self) -> None:
        if not ocr_available():
            pytest.skip("Tesseract is not installed in this environment")
        pages = extract_pages(
            sample("scanned_facilities_procedure.pdf"), ocr_enabled=True, min_chars_per_page=40
        )
        assert any(p.ocr_used for p in pages), "the image-only sample was not OCRed"
        text = " ".join(p.text for p in pages)
        # OCR is lossy, so match on a phrase rather than exact punctuation.
        assert "Visitor" in text or "visitor" in text
        assert "badge" in text.lower()

    def test_ocr_can_be_disabled(self) -> None:
        pages = extract_pages(sample("scanned_facilities_procedure.pdf"), ocr_enabled=False)
        assert not any(p.ocr_used for p in pages)

    def test_garbage_input_raises_a_meaningful_error(self) -> None:
        with pytest.raises(PdfExtractionError) as exc:
            extract_pages(b"this is definitely not a pdf file", ocr_enabled=False)
        assert "could not be read as a PDF" in str(exc.value)

    def test_error_message_does_not_echo_the_file(self) -> None:
        with pytest.raises(PdfExtractionError) as exc:
            extract_pages(b"not a pdf at all", ocr_enabled=False)
        assert "not a pdf at all" not in str(exc.value)


@pytest.fixture(scope="module")
def store():
    """A real, initialised vector store shared by the pipeline tests."""
    from ingestion.vector_store import get_vector_store, reset_vector_store

    reset_vector_store()
    store = get_vector_store()
    store.initialize()
    yield store
    reset_vector_store()


# --------------------------------------------------------------------------
# Full pipeline
# --------------------------------------------------------------------------
class TestPipeline:
    def test_document_travels_end_to_end(self, store) -> None:
        result = ingest_document(sample("expense_reimbursement.pdf"), "test_expense.pdf")

        assert result.status == "success"
        assert result.chunks_created > 0
        assert result.pages_processed >= 1
        assert result.total_tokens > 0

    def test_chunks_are_retrievable_and_verbatim(self, store) -> None:
        from ingestion.embedder import get_embedder

        ingest_document(sample("hr_leave_policy.pdf"), "test_hr.pdf")
        # Scoped by document: the store is shared across this test class, and
        # this also exercises the `where` filter the search_kb tool relies on.
        hits = store.query(
            get_embedder().embed_query("How many days of annual leave?"),
            top_k=3,
            doc_name="test_hr.pdf",
        )
        assert hits, "nothing was retrievable"
        assert all(h.doc_name == "test_hr.pdf" for h in hits)

        # The contract is top-k, not top-1: the agent shows the model every
        # retrieved chunk. What matters is that the supporting text is in the
        # result set, verbatim from the document.
        retrieved = " ".join(h.text for h in hits)
        assert "25 days" in retrieved, "the answer was not in the retrieved context"
        # Verbatim: real document text, not reconstructed tokens.
        assert "accruing monthly" in retrieved or "2.08" in retrieved

    def test_metadata_is_stored_on_every_chunk(self, store) -> None:
        ingest_document(sample("it_support_policy.pdf"), "test_it.pdf")
        metadatas = store.collection.get(where={"doc_name": "test_it.pdf"}, include=["metadatas"])
        rows = metadatas["metadatas"]
        assert rows
        for row in rows:
            assert row["doc_name"] == "test_it.pdf"
            assert row["chunk_id"].startswith("test_it.pdf#p")
            assert isinstance(row["page_number"], int)

    def test_reingesting_replaces_rather_than_duplicates(self, store) -> None:
        ingest_document(sample("hr_leave_policy.pdf"), "test_replace.pdf")
        first = store.count()
        result = ingest_document(sample("hr_leave_policy.pdf"), "test_replace.pdf")
        assert result.replaced_existing is True
        assert store.count() == first

    def test_embeddings_are_normalised(self, store) -> None:
        import math

        from ingestion.embedder import get_embedder

        vector = get_embedder().embed_query("normalised?")
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-3)

    def test_empty_upload_is_rejected(self) -> None:
        with pytest.raises(IngestionError):
            ingest_document(b"", "empty.pdf")

    def test_empty_bytes_are_rejected_before_parsing(self) -> None:
        """A zero-byte file must not reach the PDF parser."""
        with pytest.raises(IngestionError, match="empty"):
            ingest_document(b"", "empty.pdf")

    def test_scanned_document_ingests_through_ocr(self, store) -> None:
        if not ocr_available():
            pytest.skip("Tesseract is not installed in this environment")
        result = ingest_document(
            sample("scanned_facilities_procedure.pdf"), "test_scanned.pdf"
        )
        assert result.status == "success"
        assert result.pages_using_ocr >= 1
        assert result.chunks_created > 0


# --------------------------------------------------------------------------
# Batch worker
# --------------------------------------------------------------------------
class TestBatchWorker:
    def test_worker_reports_no_documents_for_an_empty_directory(self, tmp_path: Path) -> None:
        from ingestion.main import main

        assert main(["--dir", str(tmp_path)]) == 0

    def test_worker_reports_a_bad_pdf_without_crashing(self, tmp_path: Path) -> None:

        from ingestion.main import main

        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"this is not a pdf")
        # A bad file must fail that document, not the whole batch.
        assert main(["--dir", str(tmp_path)]) == 1

    def test_worker_processes_a_directory_of_valid_pdfs(self, tmp_path: Path) -> None:
        from ingestion.main import main
        from ingestion.vector_store import reset_vector_store

        for name in ("hr_leave_policy.pdf", "expense_reimbursement.pdf"):
            shutil.copy(SAMPLE_DIR / name, tmp_path / name)

        reset_vector_store()
        try:
            assert main(["--dir", str(tmp_path)]) == 0
        finally:
            reset_vector_store()


class TestReingestReporting:
    """`replaced_existing` must describe this document, not the whole store."""

    def test_first_ingest_into_a_populated_store_is_not_a_replacement(
        self, store
    ) -> None:
        # The store persists between test runs, so the document name has to be
        # unique or a repeat run would find it already indexed.
        unique = f"brand_new_{uuid.uuid4().hex[:8]}.pdf"

        # Populate the store with an unrelated document first.
        ingest_document(sample("expense_reimbursement.pdf"), "other_doc.pdf")
        assert store.count() > 0

        result = ingest_document(sample("hr_leave_policy.pdf"), unique)

        # The store was non-empty, but *this* document was not in it.
        assert result.replaced_existing is False
        assert result.status == "success"

    def test_reingesting_the_same_document_is_a_replacement(self, store) -> None:
        unique = f"same_doc_{uuid.uuid4().hex[:8]}.pdf"
        ingest_document(sample("hr_leave_policy.pdf"), unique)
        result = ingest_document(sample("hr_leave_policy.pdf"), unique)
        assert result.replaced_existing is True

    def test_count_documents_scopes_to_one_document(self, store) -> None:
        a, b = f"doc_a_{uuid.uuid4().hex[:8]}.pdf", f"doc_b_{uuid.uuid4().hex[:8]}.pdf"
        ingest_document(sample("hr_leave_policy.pdf"), a)
        ingest_document(sample("expense_reimbursement.pdf"), b)

        assert store.count_documents(a) > 0
        assert store.count_documents(b) > 0
        assert store.count_documents("never_ingested.pdf") == 0
