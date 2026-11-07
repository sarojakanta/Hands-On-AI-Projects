"""
Reranking: a cross-encoder reads (query, passage) together, which is slower but more accurate than
comparing precomputed embeddings. It only reorders candidates; the relevance gate stays in the
context builder.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Sequence
from typing import Protocol

from app.core.errors import AppError

logger = logging.getLogger("app.rag.rerank")


class RerankError(AppError):
    status_code = 502
    code = "rerank_error"


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        """One relevance score in 0-1 per passage (higher is better), same order as the input."""

    def warmup(self) -> None: ...


class CrossEncoderReranker:
    name = "cross-encoder"

    def __init__(self, model_name: str, *, max_length: int = 512, batch_size: int = 16) -> None:
        self.model_name = model_name
        self._max_length = max_length
        self._batch = batch_size
        self._model = None
        self._lock = threading.Lock()

    def warmup(self) -> None:
        self._load()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import CrossEncoder

                    self._model = CrossEncoder(self.model_name, max_length=self._max_length)
                except Exception as exc:   # noqa: BLE001 - offline, bad name, missing package
                    raise RerankError(f"Reranker model '{self.model_name}' could not be loaded: {type(exc).__name__}") from exc
            return self._model

    def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        model = self._load()
        try:
            with self._lock:
                raw = model.predict([(query, p) for p in passages], batch_size=self._batch, show_progress_bar=False)
        except Exception as exc:   # noqa: BLE001
            raise RerankError(f"Reranking failed: {type(exc).__name__}") from exc
        return [1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, float(x))))) for x in raw]


def create_reranker(kind: str, model_name: str) -> Reranker | None:
    if kind == "none":
        return None
    if kind == "cross-encoder":
        return CrossEncoderReranker(model_name)
    raise ValueError(f"Unknown reranker '{kind}'.")
