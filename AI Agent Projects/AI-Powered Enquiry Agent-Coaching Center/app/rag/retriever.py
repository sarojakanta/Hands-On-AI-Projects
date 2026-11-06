"""Retrieval interface used by the agent's knowledge-base tool."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.rag.sanitize import sanitize_text
from app.rag.vector_store import VectorStore, get_vector_store

# Below this similarity we treat retrieval as "no confident match" so the
# agent admits uncertainty instead of grounding on weak hits.
MIN_SCORE = 0.15


@dataclass
class RetrievedChunk:
    text: str
    title: str
    score: float
    document_id: int | None = None


def retrieve(
    institute_id: int,
    question: str,
    top_k: int | None = None,
    store: VectorStore | None = None,
) -> list[RetrievedChunk]:
    """Retrieve relevant, tenant-scoped chunks for a question.

    Retrieved text is sanitised again as defence-in-depth before it is ever
    handed to the model.
    """
    store = store or get_vector_store()
    settings = get_settings()
    hits = store.query(institute_id, question, top_k or settings.rag_top_k)
    chunks: list[RetrievedChunk] = []
    for h in hits:
        if h["score"] < MIN_SCORE:
            continue
        chunks.append(
            RetrievedChunk(
                text=sanitize_text(h["text"]),
                title=h["title"],
                score=h["score"],
                document_id=h.get("document_id"),
            )
        )
    return chunks


def build_context(chunks: list[RetrievedChunk], max_chars: int | None = None) -> str:
    """Concatenate retrieved chunks into a grounding context block.

    ``max_chars`` defaults to ``settings.rag_context_max_chars`` when not
    supplied, so the grounding size is configurable via the environment.
    """
    if max_chars is None:
        max_chars = get_settings().rag_context_max_chars
    parts: list[str] = []
    used = 0
    for i, ch in enumerate(chunks, start=1):
        block = f"[Source {i}: {ch.title}]\n{ch.text}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
