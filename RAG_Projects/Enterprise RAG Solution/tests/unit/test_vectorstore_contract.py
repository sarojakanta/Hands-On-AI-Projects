"""
Contract tests every VectorStore backend must satisfy.

* ``chroma``   always runs, against a real embedded ChromaDB in a temp directory.
* ``pgvector`` runs only when RAG_TEST_PGVECTOR_URL points at a PostgreSQL database that has the
               pgvector extension available; otherwise it is reported as skipped (never silently passing).
"""

from __future__ import annotations

import math
import os
import uuid

import pytest

from app.vectorstore.base import VectorRecord, VectorStoreError
from app.vectorstore.chroma_store import ChromaVectorStore, to_chroma_where

PG_URL = os.environ.get("RAG_TEST_PGVECTOR_URL", "").strip()


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


A, B, C, D = unit(1, 0, 0, 0), unit(0, 1, 0, 0), unit(1, 1, 0, 0), unit(0, 0, 1, 0)


def sample_records() -> list[VectorRecord]:
    return [
        VectorRecord("a", A, "alpha text", {"doc": "d1", "version": 1, "active": True}),
        VectorRecord("b", B, "beta text", {"doc": "d1", "version": 2, "active": False}),
        VectorRecord("c", C, "gamma text", {"doc": "d2", "version": 1, "active": True}),
        VectorRecord("d", D, "delta text", {"doc": "d2", "version": 2, "active": True}),
    ]


@pytest.fixture(params=["chroma", "pgvector"])
def store(request, tmp_path):
    if request.param == "chroma":
        instance = ChromaVectorStore(tmp_path / "chroma")
    else:
        if not PG_URL:
            pytest.skip("RAG_TEST_PGVECTOR_URL not set - pgvector contract tests need a PostgreSQL database")
        from app.vectorstore.pgvector_store import PgVectorStore   # imported only when it can actually run

        instance = PgVectorStore(PG_URL)
    created: list[str] = []
    instance.__dict__["_test_created"] = created
    yield instance
    for name in list(created):
        instance.drop_collection(name)
    instance.close()


@pytest.fixture
def collection(store):
    name = f"contract_{uuid.uuid4().hex[:8]}"
    store.ensure_collection(name, dimension=4, embedding_model="test-model")
    store.__dict__["_test_created"].append(name)
    return name


# --------------------------------------------------------------------------- collections


def test_ensure_collection_is_idempotent_and_reports_its_settings(store):
    name = f"contract_{uuid.uuid4().hex[:8]}"
    store.__dict__["_test_created"].append(name)
    assert store.collection_info(name) is None

    store.ensure_collection(name, dimension=4, embedding_model="test-model")
    store.ensure_collection(name, dimension=4, embedding_model="test-model")

    assert store.collection_info(name) == {"dimension": 4, "embedding_model": "test-model"}
    assert name in store.list_collections()


def test_a_collection_cannot_be_reused_with_another_dimension(store, collection):
    with pytest.raises(VectorStoreError) as info:
        store.ensure_collection(collection, dimension=8, embedding_model="test-model")
    assert info.value.code == "vector_dimension_mismatch"


def test_drop_collection(store):
    name = f"contract_{uuid.uuid4().hex[:8]}"
    store.ensure_collection(name, dimension=4, embedding_model="m")
    store.drop_collection(name)
    assert store.collection_info(name) is None
    store.drop_collection(name)   # dropping a missing collection is a no-op


def test_querying_a_missing_collection_is_an_error(store):
    with pytest.raises(VectorStoreError) as info:
        store.query("does_not_exist_xyz", A, 3)
    assert info.value.code == "vector_collection_missing"


# --------------------------------------------------------------------------- search


def test_query_ranks_by_cosine_similarity_and_returns_text_and_typed_metadata(store, collection):
    store.upsert(collection, sample_records())

    hits = store.query(collection, A, top_k=3)

    assert [h.id for h in hits][:2] == ["a", "c"]
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert hits[1].score == pytest.approx(math.sqrt(0.5), abs=1e-3)
    assert hits[0].text == "alpha text"
    assert hits[0].metadata == {"doc": "d1", "version": 1, "active": True}
    assert isinstance(hits[0].metadata["version"], int) and isinstance(hits[0].metadata["active"], bool)
    assert len(hits) == 3


def test_top_k_larger_than_the_collection_returns_everything(store, collection):
    store.upsert(collection, sample_records())
    assert len(store.query(collection, A, top_k=50)) == 4


def test_query_on_an_empty_collection_returns_nothing(store, collection):
    assert store.query(collection, A, top_k=3) == []


def test_upsert_with_an_existing_id_replaces_the_record(store, collection):
    store.upsert(collection, sample_records())
    store.upsert(collection, [VectorRecord("a", D, "replaced", {"doc": "d9", "version": 7, "active": True})])

    assert store.count(collection) == 4
    top = store.query(collection, D, top_k=1)[0]
    assert top.id in {"a", "d"} and store.count(collection, {"doc": "d9"}) == 1


# --------------------------------------------------------------------------- filters


def test_equality_membership_and_combined_filters(store, collection):
    store.upsert(collection, sample_records())

    assert {h.id for h in store.query(collection, A, 10, {"doc": "d1"})} == {"a", "b"}
    assert {h.id for h in store.query(collection, A, 10, {"doc": {"in": ["d1", "d2"]}})} == {"a", "b", "c", "d"}
    assert {h.id for h in store.query(collection, A, 10, {"doc": "d2", "version": 2})} == {"d"}
    assert {h.id for h in store.query(collection, A, 10, {"active": True})} == {"a", "c", "d"}
    assert store.query(collection, A, 10, {"doc": "nobody"}) == []


def test_empty_membership_filter_matches_nothing(store, collection):
    store.upsert(collection, sample_records())
    assert store.query(collection, A, 10, {"doc": {"in": []}}) == []
    assert store.count(collection, {"doc": {"in": []}}) == 0


def test_count_with_and_without_filters(store, collection):
    store.upsert(collection, sample_records())
    assert store.count(collection) == 4
    assert store.count(collection, {"doc": "d1"}) == 2
    assert store.count(collection, {"doc": "d1", "version": 1}) == 1


@pytest.mark.parametrize("bad", [{"Bad Key": 1}, {"doc": ["x"]}, {"doc": {"eq": "x"}}, {"doc": None}])
def test_malformed_filters_are_rejected(store, collection, bad):
    with pytest.raises(VectorStoreError) as info:
        store.query(collection, A, 3, bad)
    assert info.value.code == "vector_invalid_filter"


# --------------------------------------------------------------------------- delete


def test_delete_removes_only_matching_records(store, collection):
    store.upsert(collection, sample_records())

    store.delete(collection, {"doc": "d1", "version": 2})

    assert store.count(collection) == 3
    assert {h.id for h in store.query(collection, A, 10)} == {"a", "c", "d"}


def test_delete_with_membership_filter(store, collection):
    store.upsert(collection, sample_records())
    store.delete(collection, {"doc": {"in": ["d1", "d2"]}})
    assert store.count(collection) == 0


def test_delete_refuses_an_empty_filter(store, collection):
    store.upsert(collection, sample_records())
    with pytest.raises(VectorStoreError):
        store.delete(collection, {})
    assert store.count(collection) == 4


# --------------------------------------------------------------------------- metadata hygiene


def test_null_metadata_values_are_dropped_and_odd_values_stringified(store, collection):
    store.upsert(collection, [VectorRecord("x", A, "t", {"doc": "d1", "page": None, "tags": ["a", "b"]})])
    hit = store.query(collection, A, 1)[0]
    assert "page" not in hit.metadata and isinstance(hit.metadata["tags"], str)


def test_empty_metadata_is_allowed(store, collection):
    store.upsert(collection, [VectorRecord("x", A, "t", {})])
    assert store.query(collection, A, 1)[0].metadata == {}


def test_invalid_metadata_keys_are_rejected(store, collection):
    with pytest.raises(VectorStoreError):
        store.upsert(collection, [VectorRecord("x", A, "t", {"Bad-Key": 1})])


# --------------------------------------------------------------------------- Chroma specifics


def test_chroma_data_survives_reopening_the_store(tmp_path):
    path = tmp_path / "persist"
    first = ChromaVectorStore(path)
    first.ensure_collection("persist_test", dimension=4, embedding_model="m")
    first.upsert("persist_test", sample_records())
    first.close()

    second = ChromaVectorStore(path)
    assert second.collection_info("persist_test") == {"dimension": 4, "embedding_model": "m"}
    assert second.count("persist_test") == 4
    assert second.query("persist_test", A, 1)[0].id == "a"


def test_chroma_data_is_readable_from_a_separate_process(tmp_path):
    """Stronger than reopening in-process (chroma may reuse cached state there): prove it is really on disk."""
    import subprocess
    import sys

    from app.core.config import PROJECT_ROOT

    path = tmp_path / "xproc"
    store = ChromaVectorStore(path)
    store.ensure_collection("persist_xproc", dimension=4, embedding_model="m")
    store.upsert("persist_xproc", sample_records())
    store.close()

    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from app.vectorstore.chroma_store import ChromaVectorStore\n"
        "s = ChromaVectorStore(Path(sys.argv[1]))\n"
        "hit = s.query('persist_xproc', [1.0, 0.0, 0.0, 0.0], 1)[0]\n"
        "print('RESULT', s.count('persist_xproc'), hit.id, hit.text)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert "RESULT 4 a alpha text" in done.stdout, done.stdout + done.stderr


def test_chroma_where_translation():
    assert to_chroma_where({}) is None
    assert to_chroma_where({"a": 1}) == {"a": {"$eq": 1}}
    assert to_chroma_where({"a": {"in": ["x", "y"]}}) == {"a": {"$in": ["x", "y"]}}
    assert to_chroma_where({"a": 1, "b": {"in": [2]}}) == {"$and": [{"a": {"$eq": 1}}, {"b": {"$in": [2]}}]}
