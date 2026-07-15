"""Chunking behaviour, including the invariants that protect retrieval quality."""

from __future__ import annotations

import pytest

from ingestion.chunker import (
    chunk_pages,
    count_tokens,
    make_chunk_id,
    word_spans,
)
from ingestion.ocr import Page


def _page(text: str, number: int = 1, ocr: bool = False) -> Page:
    return Page(page_number=number, text=text, ocr_used=ocr)


def test_chunks_respect_size_and_cover_the_page() -> None:
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_pages([_page(text)], "doc.pdf", chunk_size=50, chunk_overlap=10)

    assert len(chunks) > 1
    assert all(c.token_count <= 50 for c in chunks)
    # The union of the chunks must cover the page, not drop the tail.
    assert "word199" in chunks[-1].text


def test_chunk_text_is_verbatim_substring_of_source() -> None:
    """The stored text must be real document text, not reassembled tokens.

    Joining subword tokens back together corrupts punctuation and casing
    ("3.2" -> "3 . 2"), which silently degrades both retrieval and the context
    the LLM eventually reads.
    """
    text = "The VPN client is version 4.2 or later. Rate limit: 100 req/s. See section 3.1."
    chunks = chunk_pages([_page(text)], "doc.pdf", chunk_size=12, chunk_overlap=0)

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.text in text
    # Punctuation and casing survive intact.
    joined = " ".join(c.text for c in chunks)
    assert "4.2" in joined
    assert "VPN" in joined
    assert "req/s." in joined


def test_chunks_do_not_split_words() -> None:
    text = " ".join(f"alpha{i}beta" for i in range(80))
    chunks = chunk_pages([_page(text)], "doc.pdf", chunk_size=20, chunk_overlap=5)

    for chunk in chunks:
        # Every token in a chunk must be a whole word from the source.
        for word in chunk.text.split():
            assert word in text.split()


def test_overlap_repeats_content_between_neighbouring_chunks() -> None:
    text = " ".join(f"token{i}" for i in range(120))
    chunks = chunk_pages([_page(text)], "doc.pdf", chunk_size=40, chunk_overlap=20)

    assert len(chunks) >= 2
    tail = chunks[0].text.split()[-5:]
    assert all(word in chunks[1].text for word in tail)


def test_empty_pages_are_skipped() -> None:
    chunks = chunk_pages(
        [_page("", 1), _page("   \n ", 2), _page("real content here", 3)],
        "doc.pdf",
        chunk_size=50,
        chunk_overlap=5,
    )
    assert len(chunks) == 1
    assert chunks[0].page_number == 3


def test_metadata_is_preserved_per_page() -> None:
    pages = [_page("content on page one", 1), _page("content on page two", 2)]
    chunks = chunk_pages(pages, "policy.pdf", chunk_size=50, chunk_overlap=5)

    assert {c.page_number for c in chunks} == {1, 2}
    assert all(c.doc_name == "policy.pdf" for c in chunks)
    assert all(c.chunk_id.startswith("policy.pdf#p") for c in chunks)


def test_ocr_flag_is_carried_into_metadata() -> None:
    chunks = chunk_pages([_page("scanned text", 1, ocr=True)], "scan.pdf", chunk_size=50, chunk_overlap=5)
    assert chunks[0].extra["used_ocr"] is True
    assert chunks[0].to_chroma_metadata()["used_ocr"] is True


def test_chunk_ids_are_deterministic_so_reingest_overwrites() -> None:
    pages = [_page("some stable content for the test")]
    first = chunk_pages(pages, "doc.pdf", chunk_size=50, chunk_overlap=5)
    second = chunk_pages(pages, "doc.pdf", chunk_size=50, chunk_overlap=5)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id == make_chunk_id("doc.pdf", 1, 0)


def test_chroma_metadata_contains_only_scalar_types() -> None:
    """Chroma rejects None and nested values outright."""
    chunks = chunk_pages([_page("content", 7)], "doc.pdf", chunk_size=50, chunk_overlap=5)
    metadata = chunks[0].to_chroma_metadata()

    assert metadata["doc_name"] == "doc.pdf"
    assert metadata["page_number"] == 7
    assert all(isinstance(v, (str, int, float, bool)) for v in metadata.values())


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-1, 0), (10, 10), (10, 20), (10, -1)],
)
def test_invalid_chunking_parameters_are_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_pages([_page("text")], "doc.pdf", chunk_size=size, chunk_overlap=overlap)


def test_word_spans_index_into_source() -> None:
    text = "  alpha beta   gamma "
    spans = word_spans(text)
    assert [text[s:e] for s, e in spans] == ["alpha", "beta", "gamma"]


def test_count_tokens_uses_supplied_tokenizer() -> None:
    assert count_tokens("a b c") == 3
    assert count_tokens("a b c", tokenizer=lambda _: ["x"]) == 1
