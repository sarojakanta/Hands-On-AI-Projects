"""Create the configured vector store backend."""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.vectorstore.base import VectorStore
from app.vectorstore.chroma_store import ChromaVectorStore


def create_vector_store(settings: Settings) -> VectorStore:
    kind = settings.vector_store.strip().lower()

    if kind == "chroma":
        return ChromaVectorStore(settings.chroma_dir)

    if kind == "pgvector":
        url = settings.vector_store_url.strip()
        if not url and settings.database_dsn.startswith("postgresql"):
            url = settings.database_dsn
        if not url:
            raise ValidationFailedError(
                "VECTOR_STORE=pgvector needs VECTOR_STORE_URL (or a PostgreSQL DATABASE_URL).",
                code="pgvector_url_required",
            )
        from app.vectorstore.pgvector_store import PgVectorStore   # imported lazily: needs psycopg + PostgreSQL

        return PgVectorStore(url)

    raise ValidationFailedError(f"Unsupported vector store '{settings.vector_store}'.", code="unsupported_vector_store")
