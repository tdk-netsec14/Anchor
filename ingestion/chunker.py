"""Token-aware chunking.

Chunks are produced per page rather than across page boundaries. That costs a
little packing efficiency on documents with very short pages, and buys exact
citations: every chunk can name the page it came from, which is what a support
agent needs when escalating to a human.

The tokenizer is injected. In production the caller passes the embedding model's
own tokenizer so "500 tokens" means 500 *model* tokens; tests and offline runs
pass the cheap approximation below.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ingestion.ocr import Page

#: Splits text into word / punctuation units. Close enough to BPE token counts
#: for chunk sizing, and needs no model load.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def approximate_tokenizer(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def word_spans(text: str) -> list[tuple[int, int]]:
    """Character span of each whitespace-delimited word.

    Used when the embedding model is unavailable and exact model offsets cannot
    be obtained. One unit per word is a coarse but consistent stand-in.
    """
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def count_tokens(text: str, tokenizer: Callable[[str], list[str]] | None = None) -> int:
    return len((tokenizer or approximate_tokenizer)(text))


def _snap_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a character window out to the nearest whitespace.

    Keeps chunk text from starting or ending mid-word ("ustomers" / "empl").
    """
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


@dataclass(slots=True)
class Chunk:
    """A retrievable unit of knowledge plus the metadata needed to cite it."""

    chunk_id: str
    doc_name: str
    page_number: int
    text: str
    token_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_chroma_metadata(self) -> dict[str, str | int | float | bool]:
        """Flatten to the scalar types ChromaDB accepts as metadata.

        Chroma rejects ``None`` and nested values outright, so optional fields
        are dropped rather than serialised.
        """
        metadata: dict[str, str | int | float | bool] = {
            "doc_name": self.doc_name,
            "chunk_id": self.chunk_id,
            "page_number": int(self.page_number),
        }
        metadata.update(
            {k: v for k, v in self.extra.items() if isinstance(v, (str, int, float, bool))}
        )
        return metadata


def make_chunk_id(doc_name: str, page_number: int, index: int) -> str:
    """Deterministic id so re-ingesting a document overwrites rather than
    duplicates its chunks."""
    return f"{doc_name}#p{page_number}#c{index}"


def chunk_pages(
    pages: Iterable[Page],
    doc_name: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    span_fn: Callable[[str], list[tuple[int, int]]] | None = None,
) -> list[Chunk]:
    """Split pages into overlapping chunks of roughly ``chunk_size`` tokens.

    ``span_fn`` returns the character span of each token in a page. The chunk
    text is sliced out of the original page string rather than reassembled from
    tokens, so what gets stored is verbatim document text.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")

    resolve_spans = span_fn or word_spans
    step = chunk_size - chunk_overlap
    chunks: list[Chunk] = []

    for page in pages:
        if not page.is_usable:
            continue
        spans = resolve_spans(page.text)
        if not spans:
            continue

        for start in range(0, len(spans), step):
            window = spans[start : start + chunk_size]
            if not window:
                break
            char_start, char_end = _snap_to_word_boundaries(
                page.text, window[0][0], window[-1][1]
            )
            text = page.text[char_start:char_end].strip()
            if not text:
                continue
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(doc_name, page.page_number, start // step),
                    doc_name=doc_name,
                    page_number=page.page_number,
                    text=text,
                    token_count=len(window),
                    extra={"used_ocr": page.ocr_used},
                )
            )
            # The final window already reached the end of the page; another
            # one would only duplicate the tail under a new id.
            if start + chunk_size >= len(spans):
                break

    return chunks
