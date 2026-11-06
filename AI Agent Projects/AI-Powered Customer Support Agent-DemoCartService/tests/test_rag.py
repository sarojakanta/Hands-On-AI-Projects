"""Phase 5 RAG tests.

Covers chunking, ingestion, hybrid retrieval relevance + fusion, source
references, and the agent's policy-answer behaviour (cite the policy when
supported; admit uncertainty + offer human support when not).

The indexes are built once per session into the configured CHROMA_DIR using the
real embedding model (first run downloads it).
"""

from __future__ import annotations

import pytest

from app.rag.ingestion import _friendly_name
from app.rag.chunking import chunk_document
from app.rag.vector_store import vector_store
from app.rag.bm25_store import bm25_store
from app.rag.retriever import HybridRetriever, hybrid_retriever
from app.rag.indexer import build_customer_index
from app.agents.graph import run_agent


@pytest.fixture(scope="session", autouse=True)
def _ingested_kb():
    """Build the customer RAG index once for the whole test session."""
    return build_customer_index()


# ------------------------------------------------------------------- chunking

def test_chunking_is_token_based_and_tagged():
    text = "# Heading\n" + ("word " * 2000)
    chunks = chunk_document(
        text,
        metadata={"policy": "Test", "source_file": "t.md", "version": "1.0"},
        chunk_tokens=400,
        overlap_tokens=40,
    )
    assert len(chunks) > 1
    for c in chunks:
        assert c.source == "Test"
        assert c.source_file == "t.md"
        assert c.metadata.get("version") == "1.0"


def test_friendly_name():
    assert _friendly_name("return_policy.md") == "Return Policy"
    assert _friendly_name("faq.md") == "FAQ"


def test_ingestion_produces_chunks(_ingested_kb):
    assert _ingested_kb >= 7  # at least one chunk per document


# ------------------------------------------------------------------ retrieval

def test_vector_and_bm25_indexes_populated(_ingested_kb):
    assert vector_store.count() == _ingested_kb
    assert bm25_store.count() == _ingested_kb


def test_retrieval_returns_relevant_policy():
    passages = hybrid_retriever.retrieve("What is your return window?")
    assert passages
    sources = {p.source for p in passages}
    # Return Policy (or the FAQ that references returns) should surface.
    assert "Return Policy" in sources or "FAQ" in sources


def test_retrieval_warranty_query():
    passages = hybrid_retriever.retrieve("How long is the warranty on electronics?")
    sources = {p.source for p in passages}
    assert "Warranty Policy" in sources


def test_source_references_deduplicated():
    passages = hybrid_retriever.retrieve("return and refund timing")
    refs = HybridRetriever.source_references(passages)
    assert refs == list(dict.fromkeys(refs))  # no duplicates, order preserved


def test_bm25_catches_exact_terms():
    # BM25 should match an exact phrase/number present in the shipping policy.
    hits = bm25_store.query("INR 499 free shipping", top_k=5)
    assert any("Shipping Policy" == h["source"] for h in hits)


def test_fusion_weight_changes_candidate_pool(monkeypatch):
    # With rerank disabled, the fused hybrid score drives ranking, so the
    # vector-only and bm25-only weightings produce different top scores.
    from app.config import config

    monkeypatch.setattr(config, "RERANK_ENABLED", False)
    vector_only = HybridRetriever(vector_weight=1.0)
    bm25_only = HybridRetriever(vector_weight=0.0)
    q = "can I exchange for a different size"
    v = vector_only.retrieve(q)
    b = bm25_only.retrieve(q)
    assert v and b
    assert v[0].score != b[0].score


def test_no_support_returns_empty_with_high_threshold():
    # A nonsense query should not clear a high rerank threshold (R-5).
    passages = hybrid_retriever.retrieve(
        "asdfqwer zxcvbnm nonsense token", min_score=5.0
    )
    assert passages == []


# --------------------------------------------------- agent policy behaviour

def test_agent_return_policy_cites_source():
    out = run_agent(
        message="Can I return an item after 15 days?",
        conversation_id="c1",
        session_id="s1",
    )
    assert out.intent == "RETURN_POLICY"
    assert out.source_references  # a policy was identified
    # Source labels now include the effective date (R-5), e.g.
    # "Return Policy (effective 2025-09-01)".
    joined = " ".join(out.source_references)
    assert "Return Policy" in joined or "FAQ" in joined
    assert "effective" in joined


def test_agent_admits_uncertainty_when_unsupported():
    out = run_agent(
        message="Do you price match competitors on imported luxury watches?",
        conversation_id="c1",
        session_id="s1",
    )
    # No supporting policy passage -> no sources, and the reply offers human help.
    assert out.source_references == []
    assert "human" in out.reply.lower() or "specialist" in out.reply.lower()
