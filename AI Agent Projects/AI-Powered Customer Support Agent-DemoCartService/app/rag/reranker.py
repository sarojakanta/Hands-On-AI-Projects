"""Cross-encoder reranker (R-3).

After hybrid retrieval returns a candidate pool (top ~20), a cross-encoder
scores each (query, passage) pair for relevance and we keep the best K (4).
The model is local (sentence-transformers CrossEncoder) and loaded lazily. If
it is unavailable, we gracefully fall back to the fused hybrid score so the
pipeline still works offline.
"""

from __future__ import annotations

import threading

from app.config import config


class Reranker:
    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or config.RERANK_MODEL
        self._model = None
        self._lock = threading.Lock()
        self._unavailable = False

    def _load(self):
        if self._model is None and not self._unavailable:
            with self._lock:
                if self._model is None and not self._unavailable:
                    try:
                        from sentence_transformers import CrossEncoder

                        self._model = CrossEncoder(self._model_name)
                    except Exception:
                        self._unavailable = True
        return self._model

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """Return the best ``top_k`` candidates by rerank score.

        Each candidate dict gets a ``rerank_score`` added. Falls back to the
        existing fused score when the cross-encoder is unavailable.
        """
        if not candidates:
            return []
        if not config.RERANK_ENABLED:
            return self._by_existing_score(candidates, top_k)

        model = self._load()
        if model is None:
            return self._by_existing_score(candidates, top_k)

        pairs = [(query, c["text"]) for c in candidates]
        try:
            scores = model.predict(pairs)
        except Exception:
            return self._by_existing_score(candidates, top_k)

        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]

    @staticmethod
    def _by_existing_score(candidates: list[dict], top_k: int) -> list[dict]:
        for c in candidates:
            c.setdefault("rerank_score", c.get("_fused", c.get("score", 0.0)))
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]


reranker = Reranker()
