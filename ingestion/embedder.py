"""Local embedding generation.

Anchor embeds with ``sentence-transformers/all-MiniLM-L6-v2`` running in-process.
That model is small (~80 MB), fast on CPU, and keeps the entire retrieval path
off the network — no embedding API key, no per-token cost, no data leaving the
host. For an internal support knowledge base that trade is clearly right.

The model is loaded lazily and exactly once per process. Loading costs a second
or two and hundreds of MB of RSS, which would be wasteful on the ``/health``
path, and repeatedly re-loading it would dominate request latency.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np

from agent.config import get_settings
from agent.observability.logger import get_logger

log = get_logger(__name__)


class EmbeddingError(RuntimeError):
    """The embedding backend could not be loaded or used."""


@contextmanager
def _quiet_tokenizer_length_warnings():
    """Silence the tokenizer's "sequence length is longer than max" warning.

    That warning fires whenever a *whole page* is tokenised to measure it, even
    though page-sized text is only ever sliced up here and never encoded. It
    is expected noise, not a real truncation risk.
    """
    target = logging.getLogger("transformers.tokenization_utils_base")
    previous = target.level
    target.setLevel(logging.ERROR)
    try:
        yield
    finally:
        target.setLevel(previous)


class Embedder:
    """Thin, lazily-initialised wrapper around a SentenceTransformer model."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or get_settings().EMBEDDING_MODEL
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> Any:
        """Return the underlying model, downloading it on first use."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:  # another thread won the race
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - dependency guaranteed by image
                raise EmbeddingError(
                    "sentence-transformers is not installed; the embedding backend "
                    "cannot be loaded. Install the ingestion requirements."
                ) from exc

            log.info("embedder.loading", context={"model": self.model_name})
            try:
                self._model = SentenceTransformer(self.model_name)
                self._tokenizer = self._model.tokenizer
            except Exception as exc:
                raise EmbeddingError(
                    f"Could not load the embedding model '{self.model_name}'. "
                    "Check network access to the model host, or pre-download the "
                    "weights into the image."
                ) from exc
            log.info("embedder.loaded", context={"model": self.model_name})
        return self._model

    @property
    def tokenizer(self) -> Any | None:
        """Model tokenizer, or ``None`` before load — used for exact chunk sizing."""
        if self._tokenizer is None:
            self.load()
        return self._tokenizer

    def char_spans(self, text: str) -> list[tuple[int, int]]:
        """Character span of every model token in ``text``.

        The chunker slices the *original* string using these offsets. Joining
        token strings back together instead would corrupt the stored text
        ("3.2" -> "3 . 2", "VPN" -> "vp ##n"), which wrecks both retrieval and
        what the LLM eventually reads as context.
        """
        tokenizer = self.tokenizer
        if tokenizer is None:
            from ingestion.chunker import word_spans

            return word_spans(text)
        try:
            with _quiet_tokenizer_length_warnings():
                encoded = tokenizer(
                    text,
                    add_special_tokens=False,
                    truncation=False,
                    return_offsets_mapping=True,
                )
        except Exception as exc:  # a non-fast tokenizer cannot report offsets
            log.warning("embedder.offsets_unavailable", context={"reason": type(exc).__name__})
            from ingestion.chunker import word_spans

            return word_spans(text)
        return [(int(s), int(e)) for s, e in encoded["offset_mapping"] if e > s]

    @property
    def max_sequence_length(self) -> int | None:
        """Longest input the model will encode, in tokens.

        all-MiniLM-L6-v2 caps at 256. Anything longer is silently truncated by
        the tokenizer, which would mean half of every oversized chunk never
        reaches the vector store — so the chunker must respect this bound.
        """
        if self._model is None:
            return None
        limit = getattr(self._model, "max_seq_length", None)
        return int(limit) if limit else None

    # -- encoding ----------------------------------------------------------
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.load()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=32,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError("Failed to embed document chunks.") from exc
        return [v.astype(float).tolist() for v in np.asarray(vectors)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Process-wide embedder singleton."""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = Embedder()
    return _embedder
