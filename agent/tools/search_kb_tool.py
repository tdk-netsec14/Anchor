"""``search_kb`` - a second look at the knowledge base mid-answer.

The initial retrieval is one shot and can miss. This tool lets the agent notice
that and pull different chunks (optionally from one named document) while it is
still composing an answer, which is the cheapest way to improve recall without a
full retrieval redesign.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.observability.logger import get_logger
from agent.rag.retriever import Retriever
from agent.tools.registry import Tool, ToolContext, ToolResult
from ingestion.vector_store import VectorStoreError

log = get_logger(__name__)

MAX_QUERY_LENGTH = 500


class SearchKbArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=MAX_QUERY_LENGTH,
        description="What to search the knowledge base for.",
    )
    top_k: int = Field(default=3, ge=1, le=10, description="How many chunks to return (1-10).")
    doc_name: str | None = Field(
        default=None,
        max_length=200,
        description="Restrict the search to a single document, if known.",
    )


class SearchKbTool(Tool):
    name = "search_kb"
    description = (
        "Search the internal knowledge base for additional passages. Use this "
        "when the context you were given does not contain the answer, or to "
        "look up a related policy section before answering."
    )
    args_model = SearchKbArgs
    # Reads tenant data, so it is never called without a workspace scope.
    requires_workspace = True

    def __init__(self, retriever: Retriever | None = None) -> None:
        self._retriever = retriever

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever()
        return self._retriever

    def run(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        query = kwargs["query"]
        top_k = int(kwargs.get("top_k") or 3)
        doc_name = kwargs.get("doc_name") or None

        try:
            result = self.retriever.retrieve(
                query,
                top_k=top_k,
                doc_name=doc_name,
                workspace_id=context.workspace_id,
            )
        except VectorStoreError as exc:
            # Surface a retryable signal to the model rather than crashing.
            log.warning("tool.search_kb_store_error", context={"reason": str(exc)})
            return ToolResult(
                name=self.name,
                ok=False,
                content="The knowledge base is temporarily unavailable. Answer without it.",
            )

        if result.is_empty:
            return ToolResult(
                name=self.name,
                ok=True,
                content=(
                    "No matching passages were found. Either the knowledge base does "
                    "not cover this, or the documents have not been ingested."
                ),
            )

        lines = [
            f"[{i}] {c.doc_name}"
            + (f", page {c.page_number}" if c.page_number else "")
            + f" (relevance {c.score:.2f})\n{c.text}"
            for i, c in enumerate(result.chunks, start=1)
        ]
        # Citations travel back as metadata so the agent can add these sources
        # to the response without re-running the search.
        return ToolResult(
            name=self.name,
            ok=True,
            content="\n\n".join(lines),
            metadata={"citations": [c.citation for c in result.chunks]},
        )
