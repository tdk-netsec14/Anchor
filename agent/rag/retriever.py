"""Retrieval: embed the question, search the store, build a cited context block.

Context formatting matters as much as the search itself. Each source is emitted
with a stable ``[S1]`` tag, its document, its page and a relevance score, and the
prompt tells the model to cite those tags. That gives the output guardrail
something concrete to verify (a citation must name a source that was actually
retrieved) instead of leaving "did you make that up?" to judgement.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import get_settings
from agent.observability.logger import get_logger
from agent.rag.vector_store import RetrievedChunk, get_vector_store
from ingestion.embedder import get_embedder

log = get_logger(__name__)

#: Chunks scoring below this are dropped. Low-similarity hits are usually noise
#: and, worse, they tempt the model to answer from irrelevant context.
MIN_RELEVANCE_SCORE = 0.05

@dataclass(slots=True)
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    top_score: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.chunks


class Retriever:
    """Semantic search over the ingested knowledge base."""

    def __init__(self, top_k: int | None = None) -> None:
        self.top_k = top_k or get_settings().RETRIEVER_TOP_K

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        min_score: float = MIN_RELEVANCE_SCORE,
        doc_name: str | None = None,
    ) -> RetrievalResult:
        """Return the chunks most relevant to ``query``.

        Returns an empty result (never raises) when the knowledge base is
        empty; callers decide whether that is a refusal or an error.
        """
        store = get_vector_store()
        embedder = get_embedder()
        vector = embedder.embed_query(query)

        # Over-fetch, then trim. Filtering after the fact means the marginal
        # chunk that fails the threshold does not consume a top-k slot.
        hits = store.query(vector, top_k=max(top_k or self.top_k, self.top_k) * 2, doc_name=doc_name)
        keep = [h for h in hits if h.score >= min_score][: (top_k or self.top_k)]

        result = RetrievalResult(
            query=query,
            chunks=keep,
            top_score=keep[0].score if keep else 0.0,
        )
        log.info(
            "retrieval.completed",
            context={
                "returned": len(keep),
                "candidates": len(hits),
                "top_score": round(result.top_score, 4),
                "top_doc": keep[0].doc_name if keep else None,
            },
        )
        return result


def build_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as a numbered, citable context block."""
    if not chunks:
        return ""
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        page = f", page {chunk.page_number}" if chunk.page_number else ""
        blocks.append(
            f"[S{index}] source: {chunk.doc_name}{page} "
            f"(relevance {chunk.score:.2f}, id {chunk.chunk_id})\n{chunk.text}"
        )
    return "\n\n".join(blocks)
