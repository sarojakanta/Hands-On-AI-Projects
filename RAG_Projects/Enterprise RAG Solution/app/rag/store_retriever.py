"""
Retriever backed by the persistent vector store and the SQL metadata.

Modes
* ``vector``: cosine search only.
* ``hybrid``: vector search and BM25 keyword search, merged with reciprocal rank fusion (RRF).
  Keyword search finds exact tokens (codes, names, numbers) that embeddings blur.

Optionally a cross-encoder reranker reorders the merged candidates.

``RetrievedChunk.score`` is always the cosine similarity to the query, so the relevance threshold
keeps one meaning in every mode. Fusion and reranking only set ``rank`` (the ordering key). Chunks
found only by keyword search get their cosine computed here so they can be gated the same way;
``lexical_coverage`` lets strong keyword evidence admit a chunk the embedding model under-rates.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

import anyio.to_thread

from app.embeddings.base import EmbeddingProvider
from app.ingestion.service import IngestionService
from app.ingestion.types import ActiveChunk
from app.rag.lexical import BM25Index
from app.rag.reranker import Reranker
from app.rag.retrieval import KnowledgeBaseNotReadyError
from app.rag.types import RetrievedChunk
from app.vectorstore.base import VectorStore

logger = logging.getLogger("app.rag.retrieval")


class RetrievalResult(list):
    """A list of chunks that can also carry non-fatal warnings (for example a reranker that failed)."""

    warnings: list[str]

    def __init__(self, items=(), warnings=()):
        super().__init__(items)
        self.warnings = list(warnings)


def _dot(a, b) -> float:
    return float(sum(x * y for x, y in zip(a, b)))   # vectors are L2-normalised: dot = cosine


class StoreRetriever:
    def __init__(
        self,
        ingestion: IngestionService,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        *,
        mode: str = "vector",
        candidate_multiplier: int = 4,
        rrf_k: int = 60,
        reranker: Reranker | None = None,
    ) -> None:
        if mode not in {"vector", "hybrid"}:
            raise ValueError(f"Unknown retrieval mode '{mode}'.")
        self._ingestion = ingestion
        self._store = vector_store
        self._embedder = embedder
        self.mode = mode
        self._multiplier = max(1, candidate_multiplier)
        self._rrf_k = rrf_k
        self.reranker = reranker
        self._lex_lock = threading.Lock()
        self._lex: tuple[tuple[str, ...], BM25Index, list[ActiveChunk]] | None = None

    @property
    def is_ready(self) -> bool:
        return self._ingestion.ready

    async def retrieve(self, query: str, top_k: int) -> list[RetrievedChunk]:
        if not self.is_ready:
            raise KnowledgeBaseNotReadyError("The knowledge base is still starting up. Try again in a moment.")
        return await anyio.to_thread.run_sync(self._retrieve_sync, query, top_k)

    # ---- keyword index ---------------------------------------------------------

    def _lexical_index(self, version_ids: list[str]) -> tuple[BM25Index, list[ActiveChunk]]:
        key = tuple(sorted(version_ids))
        with self._lex_lock:
            if self._lex is None or self._lex[0] != key:
                chunks = self._ingestion.active_chunks()
                self._lex = (key, BM25Index([c.text for c in chunks]), chunks)
            return self._lex[1], self._lex[2]

    # ---- retrieval ---------------------------------------------------------------

    def _retrieve_sync(self, query: str, top_k: int) -> RetrievalResult:
        # Only vectors of ACTIVE, ready versions are searchable: this is what makes a version switch,
        # a deletion or a re-index take effect atomically (see IngestionService).
        version_ids = self._ingestion.active_version_ids()
        if not version_ids:
            return RetrievalResult()
        vector = self._embedder.embed_query(query)
        use_hybrid = self.mode == "hybrid"
        pool = top_k * self._multiplier if (use_hybrid or self.reranker) else top_k
        hits = self._store.query(self._ingestion.collection, vector, pool, {"version_id": {"in": version_ids}})

        if not use_hybrid and self.reranker is None:
            return RetrievalResult(self._from_hit(h) for h in hits)

        warnings: list[str] = []
        candidates: dict[str, RetrievedChunk] = {}
        fused: dict[str, float] = {}
        for rank, hit in enumerate(hits, start=1):
            candidates[hit.id] = self._from_hit(hit)
            fused[hit.id] = 1.0 / (self._rrf_k + rank)

        if use_hybrid:
            index, chunks = self._lexical_index(version_ids)
            positions = {c.vector_id: i for i, c in enumerate(chunks)}
            lexical_only: list[tuple[str, ActiveChunk]] = []
            for rank, lex in enumerate(index.search(query, pool), start=1):
                chunk = chunks[lex.index]
                fused[chunk.vector_id] = fused.get(chunk.vector_id, 0.0) + 1.0 / (self._rrf_k + rank)
                if chunk.vector_id not in candidates:
                    lexical_only.append((chunk.vector_id, chunk))
            if lexical_only:
                vectors = self._embedder.embed_documents([c.text for _, c in lexical_only])
                for (vid, chunk), vec in zip(lexical_only, vectors):
                    candidates[vid] = RetrievedChunk(
                        text=chunk.text, source=chunk.source, score=max(-1.0, min(1.0, _dot(vector, vec))),
                        chunk_index=chunk.ordinal, page=chunk.page, document_id=chunk.document_id,
                        version=chunk.version,
                    )
            for vid, item in list(candidates.items()):
                pos = positions.get(vid)
                coverage = index.coverage(pos, query) if pos is not None else None
                candidates[vid] = replace(item, lexical_coverage=coverage)

        ordered = sorted(candidates, key=lambda v: (-fused[v], v))
        items = [replace(candidates[v], rank=fused[v]) for v in ordered]

        if self.reranker is not None and items:
            items, note = self._rerank(query, items, top_k)
            if note:
                warnings.append(note)
        return RetrievalResult(items[:top_k], warnings)

    def _rerank(self, query: str, items: list[RetrievedChunk], top_k: int):
        window = items[: max(top_k * 3, top_k)]
        try:
            scores = self.reranker.rerank(query, [i.text for i in window])
        except Exception as exc:   # noqa: BLE001 - never lose the answer because the reranker is down
            logger.warning("rerank_failed error=%s", type(exc).__name__)
            return items, "rerank_failed"
        reranked = sorted(
            (replace(i, rank=s, rerank_score=s) for i, s in zip(window, scores)),
            key=lambda i: i.rank,
            reverse=True,
        )
        return reranked + items[len(window):], None

    @staticmethod
    def _from_hit(hit) -> RetrievedChunk:
        return RetrievedChunk(
            text=hit.text,
            source=str(hit.metadata.get("source", "")),
            score=hit.score,
            chunk_index=hit.metadata.get("ordinal"),
            page=hit.metadata.get("page"),
            document_id=hit.metadata.get("document_id"),
            version=hit.metadata.get("version"),
        )
