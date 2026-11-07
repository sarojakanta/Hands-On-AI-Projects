"""
Retriever abstraction.

The pipeline depends only on ``Retriever``. Today it is backed by the existing FAISS
engine; hybrid search, reranking and the persistent vector store plug in later by
providing another implementation - the pipeline does not change.
"""

from __future__ import annotations

from typing import Any, Protocol

import anyio.to_thread

from app.core.errors import AppError
from app.rag.types import RetrievedChunk


class KnowledgeBaseNotReadyError(AppError):
    status_code = 503
    code = "knowledge_base_not_ready"


class Retriever(Protocol):
    @property
    def is_ready(self) -> bool: ...

    async def retrieve(self, query: str, top_k: int) -> list[RetrievedChunk]: ...


class LegacyEngineRetriever:
    """Adapter around ``rag_engine.RAGEngine`` (sentence-transformers + FAISS)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @property
    def is_ready(self) -> bool:
        return bool(self._engine.is_ready)

    async def retrieve(self, query: str, top_k: int) -> list[RetrievedChunk]:
        if not self.is_ready:
            raise KnowledgeBaseNotReadyError(
                "The knowledge base is still being indexed. Try again in a moment."
            )
        # Embedding the query is CPU-bound; keep it off the event loop.
        rows = await anyio.to_thread.run_sync(self._engine.retrieve, query, top_k)
        return [
            RetrievedChunk(
                text=row["chunk"],
                source=row["source"],
                score=float(row["score"]),
                chunk_index=row.get("chunk_index"),
            )
            for row in rows
        ]
