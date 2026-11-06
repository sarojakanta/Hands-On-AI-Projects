"""Tests for the production RAG requirements R-1..R-8.

Uses the real local embedding + rerank models (downloaded on first run). The
customer index is built once for the session.
"""

from __future__ import annotations

import pytest

from app.config import config
from app.rag import ingestion
from app.rag.chunking import chunk_document
from app.rag.retriever import hybrid_retriever, HybridRetriever
from app.rag.vector_store import staff_vector_store
from app.rag.bm25_store import staff_bm25_store
from app.rag.indexer import build_customer_index, build_staff_index, maybe_reindex
from app.rag.evaluation import evaluate


@pytest.fixture(scope="module", autouse=True)
def _indexes():
    build_customer_index()
    build_staff_index()
    yield


# ---- R-1: multi-format parsing + token chunking ----

def test_r1_html_parsing(tmp_path):
    html = "<html><body><h2>Hours</h2><p>Open 10 to 9 daily.</p></body></html>"
    f = tmp_path / "store.html"
    f.write_text(html, encoding="utf-8")
    docs = ingestion.load_documents(str(tmp_path))
    assert docs and "Open 10 to 9" in docs[0]["text"]


def test_r1_xlsx_parsing(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["City", "Hours"])
    ws.append(["Mumbai", "10-22"])
    f = tmp_path / "stores.xlsx"
    wb.save(str(f))
    docs = ingestion.load_documents(str(tmp_path))
    assert docs and "Mumbai" in docs[0]["text"]


def test_r1_token_chunk_sizes():
    text = "# H\n" + ("token " * 3000)
    chunks = chunk_document(text, metadata={"policy": "X", "source_file": "x.md", "version": "1"},
                            chunk_tokens=400, overlap_tokens=40)
    assert len(chunks) > 1


# ---- R-2: metadata on every chunk ----

def test_r2_chunks_carry_full_metadata():
    chunks = ingestion.build_chunks(config.KNOWLEDGE_BASE_DIR, customer_safe_only=True)
    assert chunks
    sample = chunks[0].metadata
    for key in ("policy", "version", "region", "language", "effective_date", "customer_safe"):
        assert key in sample


# ---- R-3: top-N candidates + rerank -> best 4 ----

def test_r3_returns_at_most_top_k():
    passages = hybrid_retriever.retrieve("return window refund", top_k=4)
    assert len(passages) <= 4


def test_r3_rerank_scores_present():
    passages = hybrid_retriever.retrieve("warranty on electronics", top_k=4)
    assert passages
    # Passages are ordered by (rerank) score descending.
    scores = [p.score for p in passages]
    assert scores == sorted(scores, reverse=True)


# ---- R-4: region + language filter before ranking ----

def test_r4_region_filter_excludes_other_region():
    # No chunks exist for region 'US', so a US-scoped query returns nothing.
    passages = hybrid_retriever.retrieve("return window", region="US", language="en")
    assert passages == []


def test_r4_language_filter_excludes_other_language():
    passages = hybrid_retriever.retrieve("return window", region="IN", language="de")
    assert passages == []


def test_r4_default_region_language_returns_results():
    passages = hybrid_retriever.retrieve("return window", region="IN", language="en")
    assert passages


# ---- R-5: citation includes effective date ----

def test_r5_source_reference_includes_effective_date():
    passages = hybrid_retriever.retrieve("What is your return window?", region="IN", language="en")
    refs = HybridRetriever.source_references(passages)
    assert any("effective" in r for r in refs)


# ---- R-6: version retirement on rebuild ----

def test_r6_rebuild_retires_old_chunks():
    # A fresh rebuild resets the collection; ids embed the version so superseded
    # versions cannot linger.
    before = build_customer_index()
    after = build_customer_index()
    assert before == after  # deterministic; no accumulation of stale versions


def test_r6_maybe_reindex_force():
    assert maybe_reindex(force=True) is True


# ---- R-7: staff-only index is separate ----

def test_r7_fraud_rules_not_in_customer_index():
    # The internal fraud doc must never be retrievable from the customer index.
    passages = hybrid_retriever.retrieve("fraud high-risk signals chargeback", region="IN", language="en")
    sources = {p.source for p in passages}
    assert "Internal Fraud Rules" not in sources


def test_r7_staff_index_has_fraud_rules():
    # It DOES live in the separate staff index.
    assert staff_vector_store.count() >= 1 or staff_bm25_store.count() >= 1
    hits = staff_bm25_store.query("fraud high-risk chargeback", top_k=5)
    assert any(h["source"] == "Internal Fraud Rules" for h in hits)


# ---- R-8: retrieval quality thresholds ----

def test_r8_recall_and_faithfulness_thresholds():
    result = evaluate()
    assert result.recall_at_4 >= 0.90, f"recall@4 {result.recall_at_4:.2%} < 90%"
    assert result.faithfulness >= 0.95, f"faithfulness {result.faithfulness:.2%} < 95%"
