"""BM25 keyword search, hybrid fusion, reranking and the keyword-evidence gate."""

from __future__ import annotations

import pytest

from app.rag.context_builder import build_context
from app.rag.lexical import BM25Index, tokenize
from app.rag.reranker import RerankError
from app.rag.store_retriever import StoreRetriever
from app.rag.types import RetrievedChunk
from app.vectorstore.base import SearchHit
from tests.unit.test_ingestion_service import build_kb, utf8

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------- BM25 / tokenizer


def test_tokenizer_keeps_compound_identifiers_and_drops_stopwords():
    assert "err-7731" in tokenize("Failure ERR-7731 occurred")
    assert {"err", "7731"} <= set(tokenize("Failure ERR-7731 occurred"))
    assert tokenize("What is the profit in 2020?", drop_stopwords=True) == ["profit", "2020"]


def test_bm25_ranks_the_passage_with_the_rare_term_first():
    index = BM25Index(["the cat sat on the mat", "the quasar emits radio waves", "dogs chase the cat"])
    hits = index.search("quasar", 5)
    assert [h.index for h in hits] == [1]
    assert hits[0].coverage == pytest.approx(1.0)


def test_bm25_coverage_is_partial_when_some_terms_are_missing():
    index = BM25Index(["alpha beta gamma", "delta epsilon"])
    full = index.search("alpha beta", 5)[0]
    partial = index.search("alpha zeta", 5)[0]
    assert full.coverage == pytest.approx(1.0)
    assert 0.0 < partial.coverage < 1.0


def test_bm25_edge_cases():
    assert BM25Index([]).search("anything", 3) == []
    index = BM25Index(["alpha beta"])
    assert index.search("the of and", 3) == []     # only stopwords
    assert index.search("alpha", 0) == []
    assert index.search("unseen", 3) == []
    assert index.coverage(0, "the of") == 0.0


# ---------------------------------------------------------------- context gate


def _chunk(score, coverage=None, text="some passage text that is long enough to keep"):
    return RetrievedChunk(text=text, source="d.txt", score=score, lexical_coverage=coverage)


def test_keyword_evidence_can_admit_a_low_similarity_chunk():
    low = _chunk(0.05, coverage=1.0)
    kept = build_context([low], min_score=0.25, max_chars=2000, max_items=5, lexical_min_coverage=0.6)
    assert len(kept.items) == 1


def test_low_similarity_without_enough_keyword_evidence_is_still_dropped():
    for chunk, lex in [(_chunk(0.05, coverage=0.3), 0.6), (_chunk(0.05, coverage=None), 0.6), (_chunk(0.05, coverage=1.0), None)]:
        built = build_context([chunk], min_score=0.25, max_chars=2000, max_items=5, lexical_min_coverage=lex)
        assert built.items == [] and built.dropped_low_score == 1


def test_context_is_ordered_by_rank_when_present():
    a = RetrievedChunk(text="first passage text is here and is long", source="a", score=0.9, rank=0.1)
    b = RetrievedChunk(text="second passage text is here and is long", source="b", score=0.4, rank=0.9)
    built = build_context([a, b], min_score=0.25, max_chars=2000, max_items=5)
    assert [i.chunk.source for i in built.items] == ["b", "a"]


# ---------------------------------------------------------------- hybrid retriever

DOC_A = "Quarterly revenue growth was strong across all regions this year and the board was pleased."
DOC_B = "The onboarding checklist covers laptops, badges and accounts for every new employee."
DOC_C = "Fault ERR-7731 means the gateway lost its certificate. Renew the certificate to clear ERR-7731."


def plain_dir(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()
    return path


def hybrid_kb(tmp_path, mode="hybrid", reranker=None):
    kb = build_kb(tmp_path)
    for name, text in (("a.txt", DOC_A), ("b.txt", DOC_B), ("c.txt", DOC_C)):
        kb.service.ingest(name, utf8(text))
    kb.retriever = StoreRetriever(kb.service, kb.store, kb.embedder, mode=mode, reranker=reranker)
    return kb


def only_doc_a_from_vectors(kb, monkeypatch):
    """Make the vector side 'miss' the answer so only keyword search can find it."""
    real = kb.store.query

    def query(collection, embedding, top_k, where=None):
        return [h for h in real(collection, embedding, 50, where) if h.metadata.get("source") != "c.txt"][:top_k]

    monkeypatch.setattr(kb.store, "query", query)


async def test_keyword_search_recovers_what_vector_search_misses(tmp_path, monkeypatch):
    kb = hybrid_kb(tmp_path, mode="hybrid")
    only_doc_a_from_vectors(kb, monkeypatch)
    results = await kb.retriever.retrieve("ERR-7731", 3)
    found = [r for r in results if r.source == "c.txt"]
    assert found, "keyword hit missing from hybrid results"
    assert found[0].lexical_coverage == pytest.approx(1.0)
    assert -1.0 <= found[0].score <= 1.0 and found[0].document_id and found[0].version == 1


async def test_vector_mode_does_not_use_keyword_search(tmp_path, monkeypatch):
    kb = hybrid_kb(tmp_path, mode="vector")
    only_doc_a_from_vectors(kb, monkeypatch)
    results = await kb.retriever.retrieve("ERR-7731", 3)
    assert all(r.source != "c.txt" for r in results)
    assert all(r.lexical_coverage is None and r.rank is None for r in results)


async def test_unknown_mode_is_rejected(tmp_path):
    kb = build_kb(tmp_path)
    with pytest.raises(ValueError):
        StoreRetriever(kb.service, kb.store, kb.embedder, mode="nonsense")


async def test_keyword_index_follows_uploads_and_deletes(tmp_path):
    kb = hybrid_kb(tmp_path)
    assert not [r for r in await kb.retriever.retrieve("zorbulon", 5) if r.lexical_coverage]
    created = kb.service.ingest("z.txt", utf8("The Zorbulon budget for next year is four million credits."))
    hit = await kb.retriever.retrieve("zorbulon", 5)
    assert hit[0].source == "z.txt"
    kb.service.delete(created.document.id)
    assert all(r.source != "z.txt" for r in await kb.retriever.retrieve("zorbulon", 5))


async def test_updated_document_only_shows_its_new_text(tmp_path):
    kb = hybrid_kb(tmp_path)
    kb.service.ingest("c.txt", utf8("Fault ERR-9999 now means the disk is full and needs cleanup work."))
    old = await kb.retriever.retrieve("ERR-7731", 5)
    assert all(not (r.source == "c.txt" and r.lexical_coverage == 1.0) for r in old)
    new = await kb.retriever.retrieve("ERR-9999", 5)
    assert new[0].source == "c.txt" and new[0].version == 2


async def test_empty_knowledge_base_returns_nothing(tmp_path):
    kb = build_kb(tmp_path)
    kb.retriever = StoreRetriever(kb.service, kb.store, kb.embedder, mode="hybrid")
    assert await kb.retriever.retrieve("anything", 3) == []


# ---------------------------------------------------------------- reranker


class ReverseReranker:
    """Scores the LAST candidate highest, so the effect on ordering is unmistakable."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def rerank(self, query, passages):
        self.calls.append((query, list(passages)))
        n = len(passages)
        return [(i + 1) / (n + 1) for i in range(n)]

    def warmup(self):
        pass


class BrokenReranker(ReverseReranker):
    def rerank(self, query, passages):
        raise RerankError("model unavailable")


async def test_reranker_reorders_candidates_and_sets_scores(tmp_path):
    reranker = ReverseReranker()
    kb = hybrid_kb(tmp_path, reranker=reranker)
    plain = await hybrid_kb(plain_dir(tmp_path)).retriever.retrieve("certificate gateway revenue", 3)
    ranked = await kb.retriever.retrieve("certificate gateway revenue", 3)
    assert reranker.calls and reranker.calls[0][0] == "certificate gateway revenue"
    assert [r.source for r in ranked] == [r.source for r in reversed(plain)]
    assert all(r.rerank_score is not None and 0 < r.rerank_score < 1 for r in ranked)


async def test_reranker_failure_keeps_fused_order_and_warns(tmp_path):
    kb = hybrid_kb(tmp_path, reranker=BrokenReranker())
    plain = await hybrid_kb(plain_dir(tmp_path)).retriever.retrieve("certificate gateway revenue", 3)
    results = await kb.retriever.retrieve("certificate gateway revenue", 3)
    assert [r.source for r in results] == [r.source for r in plain]
    assert results.warnings == ["rerank_failed"]
    assert all(r.rerank_score is None for r in results)


async def test_reranker_works_in_vector_mode_too(tmp_path):
    reranker = ReverseReranker()
    kb = hybrid_kb(tmp_path, mode="vector", reranker=reranker)
    results = await kb.retriever.retrieve("certificate gateway revenue", 3)
    assert reranker.calls and all(r.rerank_score is not None for r in results)
