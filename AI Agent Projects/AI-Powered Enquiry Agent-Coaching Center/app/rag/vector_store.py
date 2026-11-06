"""ChromaDB-backed vector store with per-institute (tenant) isolation.

A single persistent collection holds every institute's chunks. Tenant
isolation is enforced by storing ``institute_id`` in each chunk's metadata
and always filtering queries by it — a query for institute A can never
return institute B's chunks.

We pass precomputed embeddings to Chroma (embedding_function=None) so the
active Embedder (OpenAI or local hashing) is the single source of truth and
the whole path works offline.
"""
from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.config.logging_config import get_logger
from app.rag.embeddings import Embedder, get_embedder

logger = get_logger(__name__)

_COLLECTION_NAME = "institute_knowledge"


class VectorStore:
    """Thin wrapper over a persistent ChromaDB collection."""

    def __init__(self, embedder: Embedder | None = None, persist_dir: str | None = None):
        settings = get_settings()
        self._embedder = embedder or get_embedder()
        self._client = chromadb.PersistentClient(
            path=persist_dir or settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # --- Write ---------------------------------------------------------
    def add_chunks(
        self,
        institute_id: int,
        document_id: int,
        title: str,
        chunks: list[str],
    ) -> int:
        """Embed and store chunks for a document. Returns chunk count."""
        if not chunks:
            return 0
        embeddings = self._embedder.embed(chunks)
        ids = [f"inst{institute_id}-doc{document_id}-ch{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "institute_id": institute_id,
                "document_id": document_id,
                "title": title,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]
        self._collection.add(
            ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas
        )
        return len(chunks)

    def delete_document(self, institute_id: int, document_id: int) -> None:
        """Remove all chunks for a document (tenant-scoped)."""
        self._collection.delete(
            where={
                "$and": [
                    {"institute_id": institute_id},
                    {"document_id": document_id},
                ]
            }
        )

    def delete_institute(self, institute_id: int) -> None:
        self._collection.delete(where={"institute_id": institute_id})

    # --- Read ----------------------------------------------------------
    def query(
        self, institute_id: int, question: str, top_k: int | None = None
    ) -> list[dict]:
        """Return the top-k most similar chunks for an institute.

        Results are ALWAYS filtered by institute_id for tenant isolation.
        """
        settings = get_settings()
        k = top_k or settings.rag_top_k
        count = self._collection.count()
        if count == 0:
            return []
        q_embedding = self._embedder.embed_one(question)
        result = self._collection.query(
            query_embeddings=[q_embedding],
            n_results=min(k, count),
            where={"institute_id": institute_id},
        )
        hits: list[dict] = []
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            hits.append(
                {
                    "text": doc,
                    "title": (meta or {}).get("title", "Document"),
                    "document_id": (meta or {}).get("document_id"),
                    # cosine distance -> similarity
                    "score": round(1.0 - float(dist), 4),
                }
            )
        return hits

    def count(self, institute_id: int | None = None) -> int:
        if institute_id is None:
            return self._collection.count()
        return len(
            self._collection.get(where={"institute_id": institute_id}).get("ids", [])
        )

    def reset(self) -> None:
        """Delete the whole collection (tests / full reindex)."""
        self._client.delete_collection(_COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )


_cached_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Return a process-wide cached vector store."""
    global _cached_store
    if _cached_store is None:
        _cached_store = VectorStore()
    return _cached_store


def reset_store_cache() -> None:
    global _cached_store
    _cached_store = None
