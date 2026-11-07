"""ChromaDB vector store (embedded, persisted on disk, cosine similarity)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.vectorstore.base import (
    Filter,
    SearchHit,
    VectorRecord,
    VectorStoreError,
    clean_metadata,
    matches_nothing,
    validate_filter,
)

logger = logging.getLogger("app.vectorstore")

UPSERT_BATCH = 500


def to_chroma_where(where: Mapping[str, Any]) -> dict[str, Any] | None:
    """Translate a normalised neutral filter to a Chroma ``where`` clause."""
    clauses = []
    for key, condition in where.items():
        if isinstance(condition, Mapping):
            clauses.append({key: {"$in": list(condition["in"])}})
        else:
            clauses.append({key: {"$eq": condition}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaVectorStore:
    name = "chroma"

    def __init__(self, path: Path) -> None:
        try:
            import chromadb
        except ImportError as exc:   # pragma: no cover - dependency is pinned in requirements.txt
            raise VectorStoreError("chromadb is not installed.") from exc
        path.mkdir(parents=True, exist_ok=True)
        # Telemetry off: nothing about the documents or usage leaves this machine.
        self._client = chromadb.PersistentClient(path=str(path), settings=chromadb.Settings(anonymized_telemetry=False))
        self._lock = threading.RLock()   # one operation at a time keeps the embedded index consistent
        self._path = path

    # ---- collections -------------------------------------------------------

    def _get(self, collection: str):
        try:
            return self._client.get_collection(collection, embedding_function=None)
        except Exception as exc:   # noqa: BLE001 - chroma raises different types across versions
            raise VectorStoreError(
                f"Vector collection '{collection}' does not exist.", code="vector_collection_missing"
            ) from exc

    def ensure_collection(self, collection: str, *, dimension: int, embedding_model: str) -> None:
        with self._lock:
            info = self.collection_info(collection)
            if info is not None:
                if info["dimension"] != dimension:
                    raise VectorStoreError(
                        f"Collection '{collection}' has dimension {info['dimension']}, expected {dimension}.",
                        code="vector_dimension_mismatch",
                    )
                return
            self._client.create_collection(
                collection,
                metadata={"hnsw:space": "cosine", "dimension": dimension, "embedding_model": embedding_model},
                embedding_function=None,
            )

    def collection_info(self, collection: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                existing = self._client.get_collection(collection, embedding_function=None)
            except Exception:   # noqa: BLE001
                return None
            meta = existing.metadata or {}
            return {"dimension": int(meta.get("dimension", 0)), "embedding_model": str(meta.get("embedding_model", ""))}

    def list_collections(self) -> list[str]:
        with self._lock:
            return sorted(getattr(c, "name", str(c)) for c in self._client.list_collections())

    def drop_collection(self, collection: str) -> None:
        with self._lock:
            try:
                self._client.delete_collection(collection)
            except Exception:   # noqa: BLE001 - dropping a missing collection is a no-op
                logger.debug("drop_collection ignored for %s", collection)

    # ---- records --------------------------------------------------------------

    def upsert(self, collection: str, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        try:
            prepared = [
                (r.id, [float(x) for x in r.embedding], r.text, clean_metadata(r.metadata)) for r in records
            ]
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_metadata", status_code=422) from exc
        with self._lock:
            col = self._get(collection)
            for start in range(0, len(prepared), UPSERT_BATCH):
                batch = prepared[start : start + UPSERT_BATCH]
                try:
                    col.upsert(
                        ids=[b[0] for b in batch],
                        embeddings=[b[1] for b in batch],
                        documents=[b[2] for b in batch],
                        # Chroma rejects empty metadata dicts; a marker keeps the record valid.
                        metadatas=[b[3] or {"_": True} for b in batch],
                    )
                except Exception as exc:   # noqa: BLE001
                    raise VectorStoreError(f"Vector upsert failed: {type(exc).__name__}") from exc

    def query(
        self, collection: str, embedding: Sequence[float], top_k: int, where: Filter | None = None
    ) -> list[SearchHit]:
        try:
            flt = validate_filter(where)
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_filter", status_code=422) from exc
        if matches_nothing(flt) or top_k < 1:
            return []
        with self._lock:
            col = self._get(collection)
            total = col.count()
            if total == 0:
                return []
            try:
                result = col.query(
                    query_embeddings=[[float(x) for x in embedding]],
                    n_results=min(top_k, total),
                    where=to_chroma_where(flt),
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc:   # noqa: BLE001
                raise VectorStoreError(f"Vector query failed: {type(exc).__name__}") from exc

        hits: list[SearchHit] = []
        ids = result["ids"][0]
        for i, record_id in enumerate(ids):
            metadata = dict((result["metadatas"][0][i] or {}))
            metadata.pop("_", None)
            distance = float(result["distances"][0][i])
            hits.append(
                SearchHit(
                    id=record_id,
                    score=max(-1.0, min(1.0, 1.0 - distance)),   # cosine distance -> similarity
                    text=result["documents"][0][i] or "",
                    metadata=metadata,
                )
            )
        return hits

    def delete(self, collection: str, where: Filter) -> None:
        try:
            flt = validate_filter(where, allow_empty=False)
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_filter", status_code=422) from exc
        if matches_nothing(flt):
            return
        with self._lock:
            col = self._get(collection)
            try:
                col.delete(where=to_chroma_where(flt))
            except Exception as exc:   # noqa: BLE001
                raise VectorStoreError(f"Vector delete failed: {type(exc).__name__}") from exc

    def count(self, collection: str, where: Filter | None = None) -> int:
        try:
            flt = validate_filter(where)
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_filter", status_code=422) from exc
        if matches_nothing(flt):
            return 0
        with self._lock:
            col = self._get(collection)
            if not flt:
                return int(col.count())
            return len(col.get(where=to_chroma_where(flt), include=[])["ids"])

    def close(self) -> None:
        # chromadb's embedded client has no explicit close; releasing the reference is enough.
        self._client = None   # type: ignore[assignment]
