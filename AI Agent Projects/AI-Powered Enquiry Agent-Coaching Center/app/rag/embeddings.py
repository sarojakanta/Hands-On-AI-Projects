"""Embedding providers.

Two interchangeable implementations behind a common interface:

* ``OpenAIEmbedder`` — real OpenAI embeddings (used when a key is configured
  and USE_MOCK_LLM is false).
* ``HashingEmbedder`` — a deterministic, dependency-free local embedder used
  for offline development and tests. It maps tokens into a fixed-size vector
  using feature hashing (the "hashing trick") with L2 normalisation, so
  cosine similarity between texts that share words is meaningful. It is NOT
  as good as a real model, but it keeps the whole RAG path runnable and
  testable with zero cost.

Pick the active embedder with ``get_embedder()``.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

from app.config import get_settings
from app.config.logging_config import get_logger

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    """Abstract embedding provider."""

    dimension: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HashingEmbedder(Embedder):
    """Deterministic local embedder using the hashing trick + TF weighting."""

    def __init__(self, dimension: int = 512) -> None:
        self.dimension = dimension

    def _tokens(self, text: str) -> list[str]:
        return _TOKEN_RE.findall((text or "").lower())

    def _hash(self, token: str) -> int:
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        return int(digest, 16)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dimension
            tokens = self._tokens(text)
            for tok in tokens:
                h = self._hash(tok)
                idx = h % self.dimension
                # Sign bit from a different part of the hash reduces collisions.
                sign = 1.0 if (h >> 1) % 2 == 0 else -1.0
                vec[idx] += sign
            # L2 normalise so cosine similarity == dot product.
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


class OpenAICompatibleEmbedder(Embedder):
    """Embeddings via any OpenAI-compatible endpoint (OpenAI or Ollama).

    The dimension is discovered from the first response.
    """

    _DEFAULT_DIM = 1536

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        from openai import OpenAI  # local import keeps offline path import-light

        kwargs = {"api_key": api_key or "not-needed"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self.dimension = self._DEFAULT_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        vectors = [item.embedding for item in resp.data]
        if vectors:
            self.dimension = len(vectors[0])
        return vectors


# Backwards-compatible alias.
OpenAIEmbedder = OpenAICompatibleEmbedder

# Ollama's OpenAI-compatible embeddings endpoint.
_OLLAMA_EMBED_BASE = "http://localhost:11434/v1"

_cached_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the active embedder based on configuration (cached).

    Follows ``settings.resolve_embedding_provider()`` (openai | ollama |
    hashing). Any failure to construct a network embedder falls back to the
    local hashing embedder so RAG always works.
    """
    global _cached_embedder
    if _cached_embedder is not None:
        return _cached_embedder

    settings = get_settings()
    provider = settings.resolve_embedding_provider()
    try:
        if provider == "openai":
            logger.info("Using OpenAI embeddings (%s).", settings.openai_embedding_model)
            _cached_embedder = OpenAICompatibleEmbedder(
                api_key=settings.openai_api_key,
                model=settings.openai_embedding_model,
                base_url=(settings.openai_base_url or None),
            )
        elif provider == "ollama":
            base = settings.ollama_base_url.strip() or _OLLAMA_EMBED_BASE
            logger.info(
                "Using Ollama embeddings (%s) at %s.",
                settings.ollama_embedding_model,
                base,
            )
            _cached_embedder = OpenAICompatibleEmbedder(
                api_key=settings.ollama_api_key or "ollama",
                model=settings.ollama_embedding_model,
                base_url=base,
            )
        else:
            logger.info("Using local HashingEmbedder (offline).")
            _cached_embedder = HashingEmbedder()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not initialise '%s' embedder (%s); using HashingEmbedder.",
            provider,
            exc,
        )
        _cached_embedder = HashingEmbedder()
    return _cached_embedder


def reset_embedder_cache() -> None:
    """Clear the cached embedder (used by tests)."""
    global _cached_embedder
    _cached_embedder = None
