"""Create the configured embedding provider."""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.embeddings.base import CachingEmbeddingProvider, EmbeddingProvider
from app.embeddings.sentence_transformers_provider import SentenceTransformerEmbedder

SUPPORTED_PROVIDERS = ("sentence-transformers",)


def create_embedder(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding_provider.strip().lower()
    if provider == "sentence-transformers":
        return CachingEmbeddingProvider(SentenceTransformerEmbedder(settings.embedding_model))
    raise ValidationFailedError(
        f"Unsupported embedding provider '{settings.embedding_provider}'. "
        f"Supported: {', '.join(SUPPORTED_PROVIDERS)}.",
        code="unsupported_embedding_provider",
    )
