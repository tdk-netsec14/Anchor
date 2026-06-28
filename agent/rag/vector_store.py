"""Agent-side access to the vector store.

A thin, documented facade over :mod:`ingestion.vector_store`. The Agent API and
the ingestion worker are separate processes that must address the *same*
collection, so the implementation lives in the shared `ingestion` package and
this module exists to keep agent-side imports pointing at agent-side names.
"""

from __future__ import annotations

from agent.observability.logger import get_logger
from ingestion.vector_store import (
    RetrievedChunk,
    VectorStore,
    VectorStoreError,
    get_vector_store,
    reset_vector_store,
)

log = get_logger(__name__)

__all__ = [
    "RetrievedChunk",
    "VectorStore",
    "VectorStoreError",
    "get_vector_store",
    "reset_vector_store",
]
