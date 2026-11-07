"""Tests for the embedding layer. The real-model test is marked `integration` (run with: pytest -m integration)."""

from __future__ import annotations

import math

import pytest

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.embeddings.base import CachingEmbeddingProvider, EmbeddingError, validate_vectors
from app.embeddings.factory import create_embedder
from app.embeddings.sentence_transformers_provider import SentenceTransformerEmbedder
from tests.embedders import HashingEmbedder


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


# --------------------------------------------------------------------------- fake embedder sanity


def test_hashing_embedder_is_deterministic_normalised_and_topical():
    embedder = HashingEmbedder()
    a = embedder.embed_query("net loss in 2020 caused by the pandemic")
    again = embedder.embed_query("net loss in 2020 caused by the pandemic")
    related = embedder.embed_query("pandemic loss 2020")
    unrelated = embedder.embed_query("sip registration credentials server")

    assert a == again and math.isclose(math.sqrt(dot(a, a)), 1.0, rel_tol=1e-9)
    assert dot(a, related) > 0.5 > dot(a, unrelated)
    assert len(a) == embedder.dimension == 64


# --------------------------------------------------------------------------- caching wrapper


def test_query_embeddings_are_cached_and_documents_are_not():
    inner = HashingEmbedder()
    cached = CachingEmbeddingProvider(inner, max_entries=8)

    first = cached.embed_query("hello world")
    second = cached.embed_query("hello world")
    assert first == second and inner.query_calls == 1
    assert (cached.hits, cached.misses) == (1, 1)

    cached.embed_documents(["a b c"])
    cached.embed_documents(["a b c"])
    assert inner.document_calls == 2


def test_cached_vectors_cannot_be_corrupted_by_callers():
    cached = CachingEmbeddingProvider(HashingEmbedder())
    vector = cached.embed_query("hello world")
    vector[0] = 999.0
    assert cached.embed_query("hello world")[0] != 999.0


def test_cache_evicts_the_least_recently_used_query():
    inner = HashingEmbedder()
    cached = CachingEmbeddingProvider(inner, max_entries=2)
    cached.embed_query("one")
    cached.embed_query("two")
    cached.embed_query("one")      # refresh "one"; "two" is now the oldest
    cached.embed_query("three")    # evicts "two"
    calls = inner.query_calls
    cached.embed_query("one")
    assert inner.query_calls == calls          # still cached
    cached.embed_query("two")
    assert inner.query_calls == calls + 1      # was evicted


def test_wrapper_exposes_the_inner_identity():
    inner = HashingEmbedder(model_id="m-x", dimension=16)
    cached = CachingEmbeddingProvider(inner)
    assert (cached.name, cached.model_id, cached.dimension) == ("fake", "m-x", 16)


# --------------------------------------------------------------------------- validation and factory


def test_validate_vectors_checks_count_and_dimension():
    validate_vectors([[1.0, 0.0], [0.0, 1.0]], 2, 2, source="t")
    with pytest.raises(EmbeddingError):
        validate_vectors([[1.0, 0.0]], 2, 2, source="t")
    with pytest.raises(EmbeddingError):
        validate_vectors([[1.0, 0.0, 0.0]], 1, 2, source="t")


def test_factory_builds_the_default_provider_without_loading_the_model():
    embedder = create_embedder(Settings(_env_file=None, embedding_model="all-MiniLM-L6-v2"))
    assert isinstance(embedder, CachingEmbeddingProvider)
    assert (embedder.name, embedder.model_id) == ("sentence-transformers", "all-MiniLM-L6-v2")
    assert embedder._inner._model is None          # lazy: nothing is downloaded or loaded until first use


def test_factory_rejects_unknown_providers():
    with pytest.raises(ValidationFailedError) as info:
        create_embedder(Settings(_env_file=None, embedding_provider="carrier-pigeon"))
    assert info.value.code == "unsupported_embedding_provider"


def test_empty_batch_needs_no_model():
    embedder = SentenceTransformerEmbedder("any-model")
    assert embedder.embed_documents([]) == []
    assert embedder._model is None


def test_a_model_that_cannot_be_loaded_gives_a_clear_error(monkeypatch):
    def cannot_load(*args, **kwargs):
        raise OSError("repository not found (internal detail)")

    monkeypatch.setattr("sentence_transformers.SentenceTransformer", cannot_load)
    embedder = SentenceTransformerEmbedder("some/missing-model")
    with pytest.raises(EmbeddingError) as info:
        embedder.warmup()
    assert "some/missing-model" in info.value.message and "internal detail" not in info.value.message


# --------------------------------------------------------------------------- the real model


@pytest.mark.integration
def test_real_sentence_transformer_model():
    embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")

    query = embedder.embed_query("What caused the net loss in 2020?")
    related = embedder.embed_documents(["The 2020 loss was caused by pandemic-related disruption."])[0]
    unrelated = embedder.embed_documents(["A SIP endpoint registers with the SIP server."])[0]

    assert embedder.dimension == 384 and len(query) == 384
    assert math.isclose(dot(query, query), 1.0, rel_tol=1e-3)         # normalised
    assert dot(query, related) > dot(query, unrelated) + 0.1

    batch = embedder.embed_documents([f"passage number {i} about topic {i}" for i in range(70)])
    assert len(batch) == 70 and all(len(v) == 384 for v in batch)      # crosses the 32-item batch boundary
