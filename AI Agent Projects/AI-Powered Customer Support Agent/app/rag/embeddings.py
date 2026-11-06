"""Local embedding model wrapper.

Uses a sentence-transformers model (default all-MiniLM-L6-v2) that runs locally
and for free - no API key and no OpenRouter/OpenAI call. The model is loaded
lazily on first use so importing this module (and booting the app) stays fast.
"""

from __future__ import annotations

import threading

from app.config import config


class EmbeddingModel:
    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or config.EMBEDDING_MODEL
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    # Imported lazily; downloads the model on first run.
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self._model_name)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a list of texts into embedding vectors (as plain lists)."""
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def encode_one(self, text: str) -> list[float]:
        result = self.encode([text])
        return result[0] if result else []


# Convenience singleton.
embedding_model = EmbeddingModel()
