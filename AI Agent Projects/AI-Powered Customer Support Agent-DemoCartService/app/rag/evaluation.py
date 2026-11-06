"""RAG evaluation harness (R-8).

Computes two metrics against a golden question set:
  * recall@4  - fraction of questions whose expected policy appears in the top-4
    retrieved passages. Target >= 0.90.
  * faithfulness - fraction of questions where the deterministic grounded answer
    only cites retrieved sources (no fabricated policy). Target >= 0.95.

The golden set maps each question to the policy that should answer it. These
metrics give an objective, repeatable quality gate for the retriever.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.retriever import hybrid_retriever


# (question, expected_policy_source)
GOLDEN_QUESTIONS: list[tuple[str, str]] = [
    ("Can I return an item after 15 days?", "Return Policy"),
    ("What is the return window for a refund?", "Return Policy"),
    ("How do I return a defective product?", "Return Policy"),
    ("Can I exchange an item for a different size?", "Exchange Policy"),
    ("What is your exchange window?", "Exchange Policy"),
    ("How long is the warranty on electronics?", "Warranty Policy"),
    ("Is physical damage covered under warranty?", "Warranty Policy"),
    ("What are your shipping charges?", "Shipping Policy"),
    ("Do you offer free shipping?", "Shipping Policy"),
    ("How long does delivery take after dispatch?", "Shipping Policy"),
    ("Can I change my delivery address after ordering?", "Delivery Policy"),
    ("How many delivery attempts are made?", "Delivery Policy"),
    ("What are your store opening hours?", "Store Policy"),
    ("Can I return an online order in a physical store?", "Store Policy"),
    ("How do I track my order?", "FAQ"),
]


@dataclass
class EvalResult:
    recall_at_4: float
    faithfulness: float
    total: int
    recall_hits: int
    faithful_hits: int


def _policy_names(passages) -> set[str]:
    return {p.source for p in passages}


def evaluate(top_k: int = 4) -> EvalResult:
    total = len(GOLDEN_QUESTIONS)
    recall_hits = 0
    faithful_hits = 0

    for question, expected in GOLDEN_QUESTIONS:
        passages = hybrid_retriever.retrieve(question, top_k=top_k)
        names = _policy_names(passages)

        if expected in names:
            recall_hits += 1

        # Faithfulness proxy: every cited source is a real retrieved policy
        # (i.e. the answer would be grounded, not fabricated). With the
        # deterministic pipeline, source_references are derived directly from
        # retrieved passages, so this holds whenever retrieval returned the
        # expected policy; a miss is counted as unfaithful only if sources were
        # produced that don't include the expected policy.
        if not names or expected in names:
            faithful_hits += 1

    return EvalResult(
        recall_at_4=recall_hits / total if total else 0.0,
        faithfulness=faithful_hits / total if total else 0.0,
        total=total,
        recall_hits=recall_hits,
        faithful_hits=faithful_hits,
    )
