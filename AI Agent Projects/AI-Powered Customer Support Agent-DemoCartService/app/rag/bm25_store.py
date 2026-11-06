"""BM25 keyword retrieval over policy chunks (R-2, R-4, R-7).

Complements vector search with exact-term matching. Chunks (with full metadata)
are persisted to a JSON file per index so the index can be rebuilt on start.
Two named indexes keep customer-safe and staff content separate (R-7); queries
can filter by region/language metadata (R-4).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import config
from app.rag.chunking import Chunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class BM25Store:
    def __init__(self, persist_dir: str | None = None, name: str = "customer") -> None:
        self._path = Path(persist_dir or config.CHROMA_DIR) / f"bm25_{name}.json"
        self._chunks: list[dict] = []
        self._bm25 = None

    def _build_index(self) -> None:
        from rank_bm25 import BM25Okapi

        corpus = [_tokenize(c["text"]) for c in self._chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def build(self, chunks: list[Chunk]) -> None:
        self._chunks = [
            {
                "id": c.id,
                "text": c.text,
                "source": c.source,
                "source_file": c.source_file,
                "metadata": dict(c.metadata or {}),
            }
            for c in chunks
        ]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._chunks), encoding="utf-8")
        self._build_index()

    def load(self) -> bool:
        if not self._path.exists():
            return False
        self._chunks = json.loads(self._path.read_text(encoding="utf-8"))
        self._build_index()
        return True

    def count(self) -> int:
        return len(self._chunks)

    def query(self, text: str, top_k: int = 20, where: dict | None = None) -> list[dict]:
        """Return up to ``top_k`` results with normalized scores; honour a
        simple equality ``where`` metadata filter (R-4)."""
        if self._bm25 is None:
            if not self.load():
                return []
        if self._bm25 is None or not self._chunks:
            return []

        scores = self._bm25.get_scores(_tokenize(text))
        if len(scores) == 0:
            return []
        max_score = max(scores) or 1.0
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        out: list[dict] = []
        for i in order:
            if scores[i] <= 0:
                continue
            c = self._chunks[i]
            meta = c.get("metadata", {})
            if where and not all(str(meta.get(k)) == str(v) for k, v in where.items()):
                continue
            out.append(
                {
                    "id": c["id"],
                    "text": c["text"],
                    "source": c["source"],
                    "source_file": c["source_file"],
                    "metadata": meta,
                    "score": float(scores[i]) / float(max_score),
                }
            )
            if len(out) >= top_k:
                break
        return out


bm25_store = BM25Store(name="customer")
staff_bm25_store = BM25Store(name="staff")
