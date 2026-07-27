"""ChromaDB persistence layer.

Shared by the ingestion worker and the Agent API: both must address the same
collection, so the client construction lives here rather than being duplicated
per service.

Embeddings are normalised on write, which makes an inner-product query
equivalent to cosine similarity and makes the distances Chroma returns directly
interpretable (0 = identical, 1 = orthogonal).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from agent.config import get_settings
from agent.observability.logger import get_logger
from ingestion.chunker import Chunk

log = get_logger(__name__)

#: ChromaDB rejects collection names outside this shape.
_VALID_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9._-]{3,512}$")


def _scope(filters: dict[str, Any] | None, workspace_id: str | None) -> dict[str, Any] | None:
    """Combine a caller's filters with the tenant filter.

    Chroma needs ``$and`` to express "this document *in this workspace*". The
    tenant condition is added here rather than at each call site so there is a
    single place where it could be forgotten, and so ``None`` is the only way
    to search across tenants.
    """
    conditions: list[dict[str, Any]] = []
    if workspace_id:
        conditions.append({"workspace_id": workspace_id})
    if filters:
        conditions.append(filters)
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


class VectorStoreError(RuntimeError):
    """The vector store could not be reached or queried."""


@dataclass(slots=True)
class RetrievedChunk:
    """A chunk returned by similarity search, with its provenance."""

    chunk_id: str
    doc_name: str
    page_number: int | None
    text: str
    distance: float

    @property
    def score(self) -> float:
        """Similarity in [0, 1]; higher is a better match."""
        return round(max(0.0, 1.0 - self.distance), 6)

    @property
    def citation(self) -> str:
        page = f", p.{self.page_number}" if self.page_number else ""
        return f"{self.doc_name}{page} [{self.chunk_id}]"


class VectorStore:
    """Thin wrapper over a persistent ChromaDB collection."""

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.persist_dir = str(persist_dir or settings.CHROMA_PERSIST_DIR)
        self.collection_name = collection_name or settings.CHROMA_COLLECTION
        self._client: Any | None = None
        self._collection: Any | None = None
        # Separate locks: `collection` reads `client` while holding its own
        # guard, so a single shared non-reentrant Lock would deadlock.
        self._client_lock = threading.Lock()
        self._collection_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
                    try:
                        self._client = chromadb.PersistentClient(
                            path=self.persist_dir,
                            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
                        )
                    except Exception as exc:
                        raise VectorStoreError(
                            f"Could not open the vector store at '{self.persist_dir}'."
                        ) from exc
        return self._client

    @property
    def collection(self) -> Any:
        if self._collection is None:
            with self._collection_lock:
                if self._collection is None:
                    if not _VALID_COLLECTION_NAME.fullmatch(self.collection_name):
                        raise VectorStoreError(
                            f"CHROMA_COLLECTION must be 3-512 characters from "
                            f"[A-Za-z0-9._-]; got '{self.collection_name}'."
                        )
                    try:
                        self._collection = self.client.get_or_create_collection(
                            name=self.collection_name,
                            metadata={"hnsw:space": "cosine"},
                        )
                    except Exception as exc:
                        raise VectorStoreError(
                            f"Could not open the ChromaDB collection '{self.collection_name}'."
                        ) from exc
        return self._collection

    def initialize(self) -> None:
        """Open the client and collection up front.

        Fails fast on a misconfigured persist directory or collection name, and
        moves the one-off Chroma start-up cost off the first user request.
        """
        _ = self.collection

    # -- writes ------------------------------------------------------------
    def upsert_chunks(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> int:
        """Insert or replace chunks. Returns the number of rows written."""
        if not chunks:
            return 0
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must be the same length")

        try:
            self.collection.upsert(
                ids=[c.chunk_id for c in chunks],
                embeddings=[list(map(float, e)) for e in embeddings],
                documents=[c.text for c in chunks],
                metadatas=[c.to_chroma_metadata() for c in chunks],
            )
        except Exception as exc:
            raise VectorStoreError("Failed to write chunks to the vector store.") from exc
        return len(chunks)

    def count_documents(self, doc_name: str, workspace_id: str | None = None) -> int:
        """How many chunks belong to one document, within one workspace."""
        try:
            return len(
                self.collection.get(
                    where=_scope({"doc_name": doc_name}, workspace_id), include=[]
                ).get("ids")
                or []
            )
        except Exception as exc:
            raise VectorStoreError(f"Could not count chunks for document '{doc_name}'.") from exc

    def delete_document(self, doc_name: str, workspace_id: str | None = None) -> None:
        """Delete every chunk of one document, scoped to its workspace.

        Scoping the filter to the workspace is what stops a delete issued by
        one tenant from removing a same-named document belonging to another.
        """
        try:
            self.collection.delete(where=_scope({"doc_name": doc_name}, workspace_id))
        except Exception as exc:
            raise VectorStoreError(f"Failed to delete document '{doc_name}'.") from exc

    def delete_document_id(self, document_id: str) -> None:
        """Delete by `documents` row id. The precise form, used by the worker
        and by document deletion, where a name may have been reused."""
        try:
            self.collection.delete(where={"document_id": document_id})
        except Exception as exc:
            raise VectorStoreError("Failed to delete the document's chunks.") from exc

    # -- reads -------------------------------------------------------------
    def query(
        self,
        embedding: Sequence[float],
        top_k: int = 4,
        doc_name: str | None = None,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """Cosine-similarity search within one workspace.

        ``workspace_id`` is the tenant filter and is the reason two tenants
        cannot read each other's documents: it is applied by the database, not
        by discarding results afterwards, so an unscoped chunk can never reach
        the caller even in principle. Passing ``None`` searches the whole
        collection, which the API only permits when no database is configured.
        """
        try:
            count = self.collection.count()
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("The vector store is not reachable.") from exc
        if count == 0:
            return []

        where = _scope({"doc_name": doc_name} if doc_name else None, workspace_id)
        n_results = max(1, min(top_k, count))
        try:
            result = self.collection.query(
                query_embeddings=[list(map(float, embedding))],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError("Vector search failed.") from exc

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        ids = (result.get("ids") or [[]])[0]

        results: list[RetrievedChunk] = []
        for idx, text in enumerate(documents):
            metadata = metadatas[idx] or {}
            results.append(
                RetrievedChunk(
                    chunk_id=ids[idx] if idx < len(ids) else str(metadata.get("chunk_id", "")),
                    doc_name=str(metadata.get("doc_name", "unknown")),
                    page_number=metadata.get("page_number"),
                    text=text or "",
                    distance=float(distances[idx]) if idx < len(distances) else 1.0,
                )
            )
        return results

    def count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception as exc:
            raise VectorStoreError("Could not read the vector store size.") from exc

    def list_documents(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """Summarise the indexed documents visible to one workspace.

        Reads only chunk metadata. Used as a cross-check against the
        `documents` table, which is the authoritative list; a name present
        here but not there means a chunk outlived its row, which is worth
        seeing on the health endpoint.
        """
        try:
            data = self.collection.get(where=_scope(None, workspace_id), include=["metadatas"])
        except Exception as exc:
            raise VectorStoreError("Could not list documents in the vector store.") from exc

        docs: dict[str, dict[str, Any]] = {}
        for metadata in data.get("metadatas") or []:
            if not metadata:
                continue
            name = metadata.get("doc_name", "unknown")
            entry = docs.setdefault(
                name,
                {"doc_name": name, "chunks": 0, "pages": set(), "ocr_used": False},
            )
            entry["chunks"] += 1
            page = metadata.get("page_number")
            if page:
                entry["pages"].add(page)
            if metadata.get("used_ocr") or metadata.get("ocr_used"):
                entry["ocr_used"] = True

        return [
            {
                "doc_name": key,
                "chunks": value["chunks"],
                "page_count": len(value["pages"]) or 1,
                "ocr_used": value["ocr_used"],
            }
            for key, value in sorted(docs.items())
        ]

    def health(self) -> dict[str, Any]:
        """Non-throwing probe used by ``GET /health``."""
        try:
            return {"status": "ok", "chunks": self.count(), "path": self.persist_dir}
        except Exception as exc:
            return {"status": "degraded", "error": type(exc).__name__}


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_vector_store() -> VectorStore:
    """Process-wide vector store singleton."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = VectorStore()
    return _store


def reset_vector_store() -> None:
    global _store
    with _store_lock:
        _store = None
