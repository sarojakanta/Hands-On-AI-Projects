"""
Embedding provider interface.

Embeddings are deliberately separate from answer generation: switching the LLM never touches
vectors, while switching the embedding model changes the vector space and therefore requires
re-indexing (documents record the model they were indexed with, see ``app.ingestion``).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

from app.core.errors import AppError


class EmbeddingError(AppError):
    status_code = 502
    code = "embedding_failed"


class EmbeddingProvider(Protocol):
    name: str        # provider family, e.g. "sentence-transformers"
    model_id: str    # model within the provider, e.g. "all-MiniLM-L6-v2"

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages (batched). Vectors are L2-normalised, so cosine similarity = dot product."""

    def embed_query(self, text: str) -> list[float]:
        """Embed one search query."""

    def warmup(self) -> None:
        """Load the model now instead of on first use."""


def validate_vectors(vectors: Sequence[Sequence[float]], expected_count: int, dimension: int, *, source: str) -> None:
    """Defensive check shared by providers: right number of vectors, each with the declared dimension."""
    if len(vectors) != expected_count:
        raise EmbeddingError(f"{source} returned {len(vectors)} vectors for {expected_count} texts.")
    for vector in vectors:
        if len(vector) != dimension:
            raise EmbeddingError(
                f"{source} returned a vector of dimension {len(vector)}; expected {dimension}."
            )


class CachingEmbeddingProvider:
    """
    Wraps a provider with a small in-memory cache for QUERY embeddings (repeated questions are common).
    Document embeddings are not cached here: they are persisted in the vector store.
    """

    def __init__(self, inner: EmbeddingProvider, max_entries: int = 512) -> None:
        self._inner = inner
        self._max = max_entries
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def warmup(self) -> None:
        self._inner.warmup()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                self.hits += 1
                return list(cached)
            self.misses += 1
        vector = self._inner.embed_query(text)
        with self._lock:
            self._cache[text] = list(vector)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return vector
