"""ChromaDB-backed knowledge retrieval with citations.

Returns document excerpts and their sources only. Nothing here generates answers.
Collections are namespaced by embedder identity, so changing the embedding model
never mixes vector spaces; ``sync_directory`` re-indexes files into the new space.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.base import session_scope
from app.db.models import KnowledgeDocument
from app.db.repositories import KnowledgeDocumentRepository
from app.knowledge.embeddings import EmbeddingProvider, build_embedder
from app.knowledge.ingest import SUPPORTED_TYPES, UnsupportedDocumentError, chunk_segments, file_type, load_segments
from app.security.sanitize import detect_injection

logger = logging.getLogger(__name__)

_KEY = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_RESERVED = {"doc_id", "title", "source", "file_type", "section", "page", "chunk_index"}
_BATCH = 64


def _scalar(value: Any) -> str | int | float | bool:
    return value if isinstance(value, (str, int, float, bool)) else str(value)


def build_where(filters: dict | None) -> dict | None:
    """Translate simple equality / membership filters into a Chroma ``where`` clause."""
    if not filters:
        return None
    clauses = []
    for key, value in filters.items():
        if not _KEY.match(str(key)):
            raise ValueError(f"Invalid metadata filter key: {key!r}")
        if isinstance(value, (list, tuple, set)):
            clauses.append({key: {"$in": [_scalar(v) for v in value]}})
        else:
            clauses.append({key: _scalar(value)})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _title_from(filename: str, text: str, kind: str) -> str:
    if kind == "md":
        m = re.search(r"^\s{0,3}#\s+(.+?)\s*$", text, re.MULTILINE)
        if m:
            return m.group(1)[:200]
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip().title()[:200] or filename


class KnowledgeRetriever:
    def __init__(self, embedder: EmbeddingProvider | None = None, persist_dir: str | None = None, client: Any = None):
        self._embedder = embedder
        self._persist_dir = persist_dir
        self._client = client
        self._lock = threading.RLock()
        self._synced = False
        self._incidents_refreshed = 0.0

    # ------------------------------------------------------------- plumbing
    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = build_embedder()
        return self._embedder

    @property
    def client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(path=self._persist_dir or settings.chroma_dir,
                                                     settings=ChromaSettings(anonymized_telemetry=False))
        return self._client

    def _collection(self, kind: str):
        return self.client.get_or_create_collection(f"{kind}_{self.embedder.identity}",
                                                    metadata={"hnsw:space": "cosine"})

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            out.extend(self.embedder.embed(texts[i:i + _BATCH]))
        return out

    # ------------------------------------------------------------ ingestion
    def ingest_bytes(self, filename: str, data: bytes, *, title: str | None = None, source: str | None = None,
                     metadata: dict | None = None, origin: str = "api") -> dict:
        """Index (or re-index) a document. Re-ingesting the same ``source`` replaces its chunks."""
        kind = file_type(filename)
        source = source or filename
        content_hash = hashlib.sha256(data).hexdigest()
        user_meta = {str(k): _scalar(v) for k, v in (metadata or {}).items()}
        for key in user_meta:
            if not _KEY.match(key) or key in _RESERVED:
                raise ValueError(f"Invalid or reserved metadata key: {key!r}")

        with self._lock:
            with session_scope() as session:
                existing = KnowledgeDocumentRepository(session).by_source(source)
                existing_id = existing.id if existing else None
                if (existing and existing.content_hash == content_hash
                        and (existing.doc_metadata or {}).get("user") == user_meta
                        and self._has_chunks(existing.id)):
                    return {"id": existing.id, "status": "unchanged", "chunks": existing.chunk_count,
                            "title": existing.title, "source": source}

            segments = load_segments(filename, data)
            chunks = chunk_segments(segments)
            if not chunks:
                raise UnsupportedDocumentError("Document contains no extractable text")
            full_text = "\n".join(s.text for s in segments)
            doc_title = title or _title_from(filename, full_text, kind)
            vectors = self._embed([c.text for c in chunks])  # embed first: a failure leaves the index untouched

            with session_scope() as session:
                repo = KnowledgeDocumentRepository(session)
                doc = repo.get(existing_id) if existing_id else None
                status = "updated" if doc else "created"
                if doc is None:
                    doc = repo.add(KnowledgeDocument(title=doc_title, source=source, file_type=kind))
                doc_id = doc.id
                coll = self._collection("kb")
                if status == "updated":
                    coll.delete(where={"doc_id": doc_id})
                metas = []
                for c in chunks:
                    meta = {**user_meta, "doc_id": doc_id, "title": doc_title, "source": source,
                            "file_type": kind, "section": c.section, "chunk_index": c.index}
                    if c.page is not None:
                        meta["page"] = c.page
                    metas.append(meta)
                coll.add(ids=[f"{doc_id}:{c.index}" for c in chunks], documents=[c.text for c in chunks],
                         embeddings=vectors, metadatas=metas)
                doc.title, doc.source, doc.file_type = doc_title, source, kind
                doc.content_hash, doc.chunk_count = content_hash, len(chunks)
                doc.doc_metadata = {"user": user_meta, "origin": origin, "embedder": self.embedder.identity}
            return {"id": doc_id, "status": status, "chunks": len(chunks), "title": doc_title, "source": source}

    def ingest_file(self, path: str | Path, *, source: str | None = None, **kw) -> dict:
        p = Path(path)
        return self.ingest_bytes(p.name, p.read_bytes(), source=source or p.as_posix(), **kw)

    def ingest_text(self, title: str, text: str, *, source: str | None = None, metadata: dict | None = None) -> dict:
        return self.ingest_bytes(f"{title}.md", text.encode("utf-8"), title=title, source=source or title,
                                 metadata=metadata, origin="api")

    def _has_chunks(self, doc_id: str) -> bool:
        try:
            return bool(self._collection("kb").get(where={"doc_id": doc_id}, limit=1)["ids"])
        except Exception:  # noqa: BLE001
            return False

    def delete_document(self, doc_id: str) -> bool:
        with self._lock, session_scope() as session:
            doc = KnowledgeDocumentRepository(session).get(doc_id)
            if doc is None:
                return False
            self._collection("kb").delete(where={"doc_id": doc_id})
            session.delete(doc)
            return True

    def list_documents(self, file_type_: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        with session_scope() as session:
            docs = KnowledgeDocumentRepository(session).list(limit=limit, offset=offset, file_type=file_type_)
            return [{"id": d.id, "title": d.title, "source": d.source, "file_type": d.file_type,
                     "chunks": d.chunk_count, "metadata": (d.doc_metadata or {}).get("user", {}),
                     "origin": (d.doc_metadata or {}).get("origin", ""), "updated_at": d.updated_at.isoformat()}
                    for d in docs]

    def sync_directory(self, directory: str | Path | None = None) -> dict:
        """Idempotently index every supported file below ``directory``; drop vanished directory docs."""
        root = Path(directory or settings.knowledge_dir)
        counts = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "failed": 0}
        seen: set[str] = set()
        with session_scope() as session:  # keep metadata supplied at upload time; sync must not erase it
            known_meta = {d.source: (d.doc_metadata or {}).get("user", {})
                          for d in session.query(KnowledgeDocument)}
        if root.exists():
            for path in sorted(root.rglob("*")):
                if (not path.is_file() or path.suffix.lower() not in SUPPORTED_TYPES
                        or any(part.startswith(".") for part in path.relative_to(root).parts)):
                    continue
                rel = path.relative_to(root).as_posix()
                seen.add(rel)
                try:
                    counts[self.ingest_file(path, source=rel, origin="dir",
                                            metadata=known_meta.get(rel))["status"]] += 1
                except Exception as exc:  # noqa: BLE001 - one bad file must not block the rest
                    counts["failed"] += 1
                    logger.warning("Could not index %s: %s", rel, type(exc).__name__)
        with session_scope() as session:
            stale = [d.id for d in session.query(KnowledgeDocument)
                     if (d.doc_metadata or {}).get("origin") == "dir" and d.source not in seen]
        for doc_id in stale:  # outside the read session: delete_document opens its own
            if self.delete_document(doc_id):
                counts["removed"] += 1
        return counts

    def ensure_indexed(self) -> None:
        """Index the knowledge directory once per process (idempotent; covers embedder changes)."""
        if self._synced:
            return
        with self._lock:
            if not self._synced:
                self.sync_directory()
                self._synced = True

    # ------------------------------------------------------------ retrieval
    def search(self, query: str, limit: int = 5, filters: dict | None = None, min_score: float | None = None,
               per_document: int = 1, relative: float = 0.6) -> list[dict]:
        """Search the knowledge base. Each hit: title, source, excerpt, score, metadata, citation, suspicious.

        ``min_score`` is an absolute cosine floor; ``relative`` drops hits scoring below that
        fraction of the best hit. Defaults are tuned for the offline hash embedder and are
        conservative for model-based embedders.
        """
        if not query or not query.strip():
            return []
        self.ensure_indexed()
        floor = settings.knowledge_min_score if min_score is None else min_score
        where = build_where(filters)
        with self._lock:
            coll = self._collection("kb")
            count = coll.count()
            if count == 0:
                return []
            vector = self._embed([query])[0]
            res = coll.query(query_embeddings=[vector], n_results=min(max(limit * 4, limit), count, 50),
                             where=where, include=["documents", "metadatas", "distances"])
        hits, per_doc = [], {}
        for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0], strict=False):
            score = max(0.0, 1.0 - float(dist))
            if score < floor:
                continue
            doc_id = meta.get("doc_id", "")
            if per_document and per_doc.get(doc_id, 0) >= per_document:
                continue
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            citation = meta.get("title", "")
            if meta.get("section") and meta["section"] != citation:
                citation += f" > {meta['section']}"
            if meta.get("page") is not None:
                citation += f" (p.{meta['page']})"
            hits.append({"title": meta.get("title", ""), "source": meta.get("source", ""), "excerpt": text,
                         "score": round(score, 3), "metadata": dict(meta), "citation": f"{citation} [{meta.get('source', '')}]",
                         "suspicious": detect_injection(text)})
        hits.sort(key=lambda h: h["score"], reverse=True)
        if hits and relative:  # drop tail noise far below the best match
            hits = [h for h in hits if h["score"] >= hits[0]["score"] * relative]
        return hits[:limit]

    # ------------------------------------------------- historical incidents
    def refresh_incidents(self, records: list, ttl: float = 300.0, force: bool = False) -> int:
        """Index ServiceNow incidents so similar past incidents can be retrieved. Idempotent."""
        if not force and time.time() - self._incidents_refreshed < ttl:
            return 0
        with self._lock:
            coll = self._collection("incidents")
            ids = [r.number for r in records]
            known = coll.get(ids=ids, include=["metadatas"]) if ids else {"ids": [], "metadatas": []}
            old = {i: (m or {}).get("h") for i, m in zip(known["ids"], known["metadatas"], strict=False)}
            todo, texts = [], []
            for r in records:
                text = f"{r.short_description}\n{r.description}".strip()
                digest = hashlib.sha1(f"{text}|{r.state}|{r.priority}".encode(), usedforsecurity=False).hexdigest()
                if old.get(r.number) != digest:
                    todo.append((r, digest))
                    texts.append(text)
            if todo:
                coll.upsert(ids=[r.number for r, _ in todo], documents=texts, embeddings=self._embed(texts),
                            metadatas=[{"number": r.number, "short_description": r.short_description[:300],
                                        "state": r.state, "priority": r.priority, "category": r.category,
                                        "h": h} for r, h in todo])
            self._incidents_refreshed = time.time()
            return len(todo)

    def similar_incidents(self, text: str, exclude: str | None = None, limit: int = 3,
                          extended: bool = False) -> list[dict]:
        if not text.strip():
            return []
        floor = settings.similar_min_score / (2 if extended else 1)
        with self._lock:
            coll = self._collection("incidents")
            count = coll.count()
            if count == 0:
                return []
            res = coll.query(query_embeddings=[self._embed([text])[0]], n_results=min(limit + 1, count),
                             include=["metadatas", "distances"])
        out = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0], strict=False):
            score = max(0.0, 1.0 - float(dist))
            if meta["number"] == exclude or score < floor:
                continue
            out.append({"number": meta["number"], "short_description": meta.get("short_description", ""),
                        "state": meta.get("state", ""), "score": round(score, 3),
                        "source": f"incident:{meta['number']}"})
        return out[:limit]

    def stats(self) -> dict:
        with self._lock:
            return {"embedder": self.embedder.identity, "chunks": self._collection("kb").count(),
                    "incidents": self._collection("incidents").count()}
