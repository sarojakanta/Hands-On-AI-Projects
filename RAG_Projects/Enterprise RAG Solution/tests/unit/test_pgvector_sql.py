"""SQL generation for the pgvector adapter. No database is needed or used."""

import pytest

from app.vectorstore.base import VectorStoreError
from app.vectorstore.pgvector_store import build_where, table_name, vector_literal


def test_empty_filter_has_no_where():
    assert build_where({}) == ("", {})


def test_equality_and_in_are_bound_parameters():
    sql, params = build_where({"version_id": {"in": ["a", "b"]}, "page": 3, "ok": True})
    assert sql == (
        " WHERE metadata->>:k0 = ANY(:v0) AND metadata->>:k1 = :v1 AND metadata->>:k2 = :v2"
    )
    assert params == {"k0": "version_id", "v0": ["a", "b"], "k1": "page", "v1": "3", "k2": "ok", "v2": "true"}


def test_values_never_appear_in_sql_text():
    sql, _ = build_where({"document_id": "x'; DROP TABLE y;--"})
    assert "DROP" not in sql and "x'" not in sql


def test_table_name_is_quoted_and_validated():
    assert table_name("chunks_all-minilm-l6-v2_384") == '"vs_chunks_all-minilm-l6-v2_384"'
    for bad in ['x"; DROP TABLE a;--', "", "a b", "a" * 64]:
        with pytest.raises(VectorStoreError):
            table_name(bad)


def test_vector_literal():
    assert vector_literal([1, 0.5]) == "[1.0,0.5]"
