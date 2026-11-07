"""Deterministic fake embedders for tests (no model download, no network)."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from app.embeddings.base import EmbeddingError

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """
    Bag-of-words hashing embedder. Texts that share words get a high cosine similarity and unrelated
    texts score near zero, which is enough to test retrieval behaviour deterministically.
    """

    name = "fake"

    def __init__(self, model_id: str = "fake-bow-64", dimension: int = 64) -> None:
        self.model_id = model_id
        self._dimension = dimension
        self.document_calls = 0
        self.query_calls = 0
        self.fail_next_documents = False

    @property
    def dimension(self) -> int:
        return self._dimension

    def warmup(self) -> None:
        return None

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _TOKEN.findall(text.lower()):
            bucket = int(hashlib.sha256(token.encode()).hexdigest(), 16) % self._dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        if self.fail_next_documents:
            self.fail_next_documents = False
            raise EmbeddingError("simulated embedding failure")
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vector(text)
