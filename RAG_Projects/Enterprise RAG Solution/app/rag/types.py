"""Data types shared by the RAG components."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.base import Usage


@dataclass(frozen=True)
class RetrievedChunk:
    """One passage returned by a retriever."""

    text: str
    source: str                      # document name
    score: float                     # similarity (higher is better)
    chunk_index: int | None = None   # position of the chunk inside its document
    page: int | None = None          # page number when the format has pages
    document_id: str | None = None   # stable id of the document (persistent storage)
    version: int | None = None       # version number of the document this passage came from
    rank: float | None = None        # ordering key set by hybrid fusion / reranking; falls back to ``score``
    lexical_coverage: float | None = None   # share (0-1) of the query's keyword weight found in the passage
    rerank_score: float | None = None       # cross-encoder relevance (0-1) when a reranker ran

    @property
    def sort_key(self) -> float:
        return self.score if self.rank is None else self.rank

    @property
    def chunk_id(self) -> str | None:
        """Human-readable chunk reference. Replaced by stable database IDs when persistent storage lands."""
        return None if self.chunk_index is None else f"{self.source}#{self.chunk_index}"


@dataclass(frozen=True)
class ContextItem:
    """A chunk selected for the prompt, with the number the LLM must use to cite it."""

    number: int
    chunk: RetrievedChunk


@dataclass
class BuiltContext:
    items: list[ContextItem] = field(default_factory=list)
    dropped_low_score: int = 0
    dropped_duplicates: int = 0
    dropped_budget: int = 0
    truncated_first: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.items


@dataclass(frozen=True)
class Citation:
    """A reference the answer actually used. Only ever created for a real context item."""

    number: int
    document: str
    chunk_id: str | None
    page: int | None
    score: float
    excerpt: str
    document_id: str | None = None
    version: int | None = None


@dataclass
class RagAnswer:
    conversation_id: str
    message_id: str
    question: str
    standalone_query: str
    answer: str
    found_in_documents: bool
    citations: list[Citation]
    retrieved: list[ContextItem]          # everything that was given to the LLM
    provider: str | None = None           # None when no LLM call was needed
    model: str | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    used_fallback: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None
    attempts: int = 1
    rewrite_failed: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
