"""
PostgreSQL + pgvector vector store.

NOT verified against a live PostgreSQL in this repository's default test run: the shared contract
tests in ``tests/unit/test_vectorstore_contract.py`` execute against it only when
``RAG_TEST_PGVECTOR_URL`` is set. SQL generation is unit tested without a database.

Layout: a registry table ``vs_collections`` and one table ``vs_<collection>`` per collection with
``id text primary key, embedding vector(<dim>), text text, metadata jsonb``. Cosine distance (``<=>``).
Metadata filters compare ``metadata->>'key'`` as text, so numbers and booleans are compared by their
text form (``True`` -> ``true``). Keys are validated and values are always bound parameters.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping, Sequence
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

UPSERT_BATCH = 500
_COLLECTION = re.compile(r"^[A-Za-z0-9_\-]{1,63}$")
REGISTRY = "vs_collections"


def table_name(collection: str) -> str:
    """Quoted table identifier for a collection. The name is restricted so quoting is sufficient."""
    if not _COLLECTION.match(collection):
        raise VectorStoreError(f"Invalid collection name: {collection!r}", code="vector_invalid_collection", status_code=422)
    return '"vs_' + collection + '"'


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_where(where: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate a normalised neutral filter to ``(" WHERE ...", params)``; empty filter -> ``("", {})``."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, (key, condition) in enumerate(where.items()):
        params[f"k{i}"] = key
        if isinstance(condition, Mapping):
            params[f"v{i}"] = [_text(v) for v in condition["in"]]
            clauses.append(f"metadata->>:k{i} = ANY(:v{i})")
        else:
            params[f"v{i}"] = _text(condition)
            clauses.append(f"metadata->>:k{i} = :v{i}")
    return ((" WHERE " + " AND ".join(clauses)) if clauses else ""), params


def vector_literal(embedding: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class PgVectorStore:
    name = "pgvector"

    def __init__(self, url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:   # pragma: no cover
            raise VectorStoreError("SQLAlchemy is not installed.") from exc
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        try:
            self._engine = create_engine(url, pool_pre_ping=True, future=True)
        except ModuleNotFoundError as exc:
            raise VectorStoreError(
                "A PostgreSQL driver is required: python -m pip install 'psycopg[binary]'", code="vector_driver_missing"
            ) from exc
        self._text = text
        self._lock = threading.RLock()
        self._initialised = False

    # ---- helpers -------------------------------------------------------------

    def _init(self) -> None:
        if self._initialised:
            return
        t = self._text
        try:
            with self._engine.begin() as conn:
                conn.execute(t("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.execute(
                    t(
                        f"CREATE TABLE IF NOT EXISTS {REGISTRY} ("
                        "name text PRIMARY KEY, dimension integer NOT NULL, embedding_model text NOT NULL)"
                    )
                )
        except Exception as exc:   # noqa: BLE001
            raise VectorStoreError(f"PostgreSQL/pgvector initialisation failed: {type(exc).__name__}") from exc
        self._initialised = True

    def _run(self, fn):
        with self._lock:
            self._init()
            try:
                return fn()
            except VectorStoreError:
                raise
            except Exception as exc:   # noqa: BLE001
                raise VectorStoreError(f"Vector operation failed: {type(exc).__name__}") from exc

    def _require(self, conn, collection: str) -> int:
        row = conn.execute(self._text(f"SELECT dimension FROM {REGISTRY} WHERE name = :n"), {"n": collection}).first()
        if row is None:
            raise VectorStoreError(
                f"Vector collection '{collection}' does not exist.", code="vector_collection_missing"
            )
        return int(row[0])

    @staticmethod
    def _filter(where: Filter | None, *, allow_empty: bool = True) -> dict[str, Any]:
        try:
            return validate_filter(where, allow_empty=allow_empty)
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_filter", status_code=422) from exc

    # ---- collections -------------------------------------------------------------

    def ensure_collection(self, collection: str, *, dimension: int, embedding_model: str) -> None:
        table = table_name(collection)

        def op():
            t = self._text
            with self._engine.begin() as conn:
                row = conn.execute(t(f"SELECT dimension FROM {REGISTRY} WHERE name = :n"), {"n": collection}).first()
                if row is not None:
                    if int(row[0]) != dimension:
                        raise VectorStoreError(
                            f"Collection '{collection}' has dimension {row[0]}, expected {dimension}.",
                            code="vector_dimension_mismatch",
                        )
                    return
                conn.execute(
                    t(
                        f"CREATE TABLE IF NOT EXISTS {table} (id text PRIMARY KEY, "
                        f"embedding vector({int(dimension)}) NOT NULL, text text NOT NULL, "
                        "metadata jsonb NOT NULL DEFAULT '{}'::jsonb)"
                    )
                )
                conn.execute(
                    t(
                        f"INSERT INTO {REGISTRY} (name, dimension, embedding_model) VALUES (:n, :d, :m) "
                        "ON CONFLICT (name) DO NOTHING"
                    ),
                    {"n": collection, "d": dimension, "m": embedding_model},
                )

        self._run(op)

    def collection_info(self, collection: str) -> dict[str, Any] | None:
        def op():
            with self._engine.connect() as conn:
                row = conn.execute(
                    self._text(f"SELECT dimension, embedding_model FROM {REGISTRY} WHERE name = :n"), {"n": collection}
                ).first()
            return None if row is None else {"dimension": int(row[0]), "embedding_model": str(row[1])}

        return self._run(op)

    def list_collections(self) -> list[str]:
        def op():
            with self._engine.connect() as conn:
                return sorted(r[0] for r in conn.execute(self._text(f"SELECT name FROM {REGISTRY}")))

        return self._run(op)

    def drop_collection(self, collection: str) -> None:
        table = table_name(collection)

        def op():
            with self._engine.begin() as conn:
                conn.execute(self._text(f"DROP TABLE IF EXISTS {table}"))
                conn.execute(self._text(f"DELETE FROM {REGISTRY} WHERE name = :n"), {"n": collection})

        self._run(op)

    # ---- records -------------------------------------------------------------------

    def upsert(self, collection: str, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        table = table_name(collection)
        try:
            prepared = [
                {"id": r.id, "e": vector_literal(r.embedding), "t": r.text, "m": json.dumps(clean_metadata(r.metadata))}
                for r in records
            ]
        except ValueError as exc:
            raise VectorStoreError(str(exc), code="vector_invalid_metadata", status_code=422) from exc

        def op():
            sql = self._text(
                f"INSERT INTO {table} (id, embedding, text, metadata) "
                "VALUES (:id, CAST(:e AS vector), :t, CAST(:m AS jsonb)) "
                "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, "
                "text = EXCLUDED.text, metadata = EXCLUDED.metadata"
            )
            with self._engine.begin() as conn:
                dim = self._require(conn, collection)
                if any(len(r.embedding) != dim for r in records):
                    raise VectorStoreError(
                        f"Embedding dimension does not match collection dimension {dim}.",
                        code="vector_dimension_mismatch",
                    )
                for start in range(0, len(prepared), UPSERT_BATCH):
                    conn.execute(sql, prepared[start : start + UPSERT_BATCH])

        self._run(op)

    def query(
        self, collection: str, embedding: Sequence[float], top_k: int, where: Filter | None = None
    ) -> list[SearchHit]:
        table = table_name(collection)
        flt = self._filter(where)
        if matches_nothing(flt) or top_k < 1:
            return []
        clause, params = build_where(flt)
        params.update(e=vector_literal(embedding), k=int(top_k))

        def op():
            sql = self._text(
                f"SELECT id, text, metadata, 1 - (embedding <=> CAST(:e AS vector)) AS score "
                f"FROM {table}{clause} ORDER BY embedding <=> CAST(:e AS vector) LIMIT :k"
            )
            with self._engine.connect() as conn:
                self._require(conn, collection)
                rows = conn.execute(sql, params).all()
            hits = []
            for rid, txt, meta, score in rows:
                if isinstance(meta, str):
                    meta = json.loads(meta)
                hits.append(
                    SearchHit(id=rid, score=max(-1.0, min(1.0, float(score))), text=txt, metadata=dict(meta or {}))
                )
            return hits

        return self._run(op)

    def delete(self, collection: str, where: Filter) -> None:
        table = table_name(collection)
        flt = self._filter(where, allow_empty=False)
        if matches_nothing(flt):
            return
        clause, params = build_where(flt)

        def op():
            with self._engine.begin() as conn:
                self._require(conn, collection)
                conn.execute(self._text(f"DELETE FROM {table}{clause}"), params)

        self._run(op)

    def count(self, collection: str, where: Filter | None = None) -> int:
        table = table_name(collection)
        flt = self._filter(where)
        if matches_nothing(flt):
            return 0
        clause, params = build_where(flt)

        def op():
            with self._engine.connect() as conn:
                self._require(conn, collection)
                return int(conn.execute(self._text(f"SELECT count(*) FROM {table}{clause}"), params).scalar_one())

        return self._run(op)

    def close(self) -> None:
        self._engine.dispose()
