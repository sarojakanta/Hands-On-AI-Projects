"""Hybrid retriever with region/language filtering and reranking (R-3, R-4, R-5).

Pipeline:
  1. Filter candidates by region + language BEFORE ranking (R-4) so a customer
     only ever sees their region's rules.
  2. Pull a large candidate pool (default 20) from vector + BM25 and fuse (R-3).
  3. Cross-encoder rerank the pool and keep the best K (default 4) (R-3).
  4. Return passages with policy name + effective date for citation (R-5).

If the best passage's rerank score is below the threshold, the caller treats it
as "no supporting passage" and the agent admits uncertainty + offers a human.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import config
from app.rag.vector_store import vector_store
from app.rag.bm25_store import bm25_store
from app.rag.reranker import reranker


@dataclass
class RetrievedPassage:
    id: str
    text: str
    source: str
    source_file: str
    score: float
    region: str = ""
    language: str = ""
    version: str = ""
    effective_date: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "source_file": self.source_file,
            "score": round(self.score, 4),
            "region": self.region,
            "language": self.language,
            "version": self.version,
            "effective_date": self.effective_date,
        }


class HybridRetriever:
    def __init__(self, vector_weight: float | None = None, top_k: int | None = None) -> None:
        vw = config.HYBRID_VECTOR_WEIGHT if vector_weight is None else vector_weight
        self._vector_weight = min(max(vw, 0.0), 1.0)
        self._top_k = top_k or config.RETRIEVAL_TOP_K

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        region: str | None = None,
        language: str | None = None,
        min_score: float | None = None,
    ) -> list[RetrievedPassage]:
        k = top_k or self._top_k
        pool = config.RETRIEVAL_CANDIDATES
        threshold = config.RERANK_MIN_SCORE if min_score is None else min_score

        # R-4: region + language filter applied in the query (before ranking).
        where = self._build_filter(region, language)

        vector_hits = vector_store.query(query, top_k=pool, where=where)
        bm25_hits = bm25_store.query(query, top_k=pool, where=where)

        # Fuse the two candidate lists (R-3 step 1).
        vw, bw = self._vector_weight, 1.0 - self._vector_weight
        fused: dict[str, dict] = {}
        for hit in vector_hits:
            fused[hit["id"]] = {**hit, "_fused": vw * hit["score"]}
        for hit in bm25_hits:
            if hit["id"] in fused:
                fused[hit["id"]]["_fused"] += bw * hit["score"]
            else:
                fused[hit["id"]] = {**hit, "_fused": bw * hit["score"]}

        candidates = sorted(fused.values(), key=lambda h: h["_fused"], reverse=True)[:pool]

        # R-3 step 2: cross-encoder rerank, keep best K.
        reranked = reranker.rerank(query, candidates, top_k=k)

        passages: list[RetrievedPassage] = []
        for h in reranked:
            if h.get("rerank_score", 0.0) < threshold:
                continue
            meta = h.get("metadata", {}) or {}
            passages.append(
                RetrievedPassage(
                    id=h["id"],
                    text=h["text"],
                    source=h.get("source") or meta.get("policy", ""),
                    source_file=h.get("source_file", ""),
                    score=h.get("rerank_score", h.get("_fused", 0.0)),
                    region=meta.get("region", ""),
                    language=meta.get("language", ""),
                    version=meta.get("version", ""),
                    effective_date=meta.get("effective_date", ""),
                )
            )
        return passages

    @staticmethod
    def _build_filter(region: str | None, language: str | None) -> dict | None:
        region = region if region is not None else config.DEFAULT_REGION
        language = language if language is not None else config.DEFAULT_LANGUAGE
        clauses = []
        if region:
            clauses.append({"region": region})
        if language:
            clauses.append({"language": language})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    @staticmethod
    def source_references(passages: list[RetrievedPassage]) -> list[str]:
        """De-duplicated source names WITH effective date for citation (R-5)."""
        seen: list[str] = []
        for p in passages:
            label = p.source
            if p.effective_date and p.effective_date != "unknown":
                label = f"{p.source} (effective {p.effective_date})"
            if label and label not in seen:
                seen.append(label)
        return seen


hybrid_retriever = HybridRetriever()
