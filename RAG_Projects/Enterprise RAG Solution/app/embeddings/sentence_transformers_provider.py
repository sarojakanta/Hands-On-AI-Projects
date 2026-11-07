"""Local sentence-transformers embeddings (default: all-MiniLM-L6-v2, 384 dimensions)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from app.embeddings.base import EmbeddingError, validate_vectors

logger = logging.getLogger("app.embeddings")

BATCH_SIZE = 32


class SentenceTransformerEmbedder:
    name = "sentence-transformers"

    def __init__(self, model_id: str = "all-MiniLM-L6-v2", batch_size: int = BATCH_SIZE) -> None:
        self.model_id = model_id
        self._batch_size = batch_size
        self._model: Any = None
        self._dimension: int | None = None
        self._load_lock = threading.Lock()
        # Tokenizers are not safe for concurrent use ("Already borrowed"); batches are short, so serialising
        # encode() calls keeps queries responsive while an upload is being indexed.
        self._encode_lock = threading.Lock()

    def _ensure_model(self) -> Any:
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    logger.info("embedding_model_loading model=%s", self.model_id)
                    try:
                        from sentence_transformers import SentenceTransformer   # heavy import, done lazily

                        model = SentenceTransformer(self.model_id)
                    except Exception as exc:   # noqa: BLE001 - any load failure must surface as one clear error
                        raise EmbeddingError(
                            f"Could not load embedding model '{self.model_id}': {type(exc).__name__}"
                        ) from exc
                    self._dimension = int(model.get_sentence_embedding_dimension())
                    self._model = model
        return self._model

    @property
    def dimension(self) -> int:
        self._ensure_model()
        assert self._dimension is not None
        return self._dimension

    def warmup(self) -> None:
        self._ensure_model()

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure_model()
        try:
            with self._encode_lock:
                array = model.encode(
                    list(texts),
                    batch_size=self._batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
        except Exception as exc:   # noqa: BLE001
            raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc
        vectors = [[float(x) for x in row] for row in array]
        validate_vectors(vectors, len(texts), self.dimension, source=self.model_id)
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        # One lock acquisition per batch lets query embeddings interleave with a long document.
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._encode(texts[start : start + self._batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text])[0]
