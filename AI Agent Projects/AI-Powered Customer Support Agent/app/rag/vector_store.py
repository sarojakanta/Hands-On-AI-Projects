"""ChromaDB vector store for policy chunks (R-2, R-4, R-7).

Each chunk's full metadata (policy, version, region, language, effective_date,
customer_safe, source_file) is stored alongside the embedding. The store
supports:
  * named collections so customer-safe and staff-only indexes are separate
    (R-7),
  * metadata ``where`` filters so region/language filtering happens in the
    query (R-4).
"""

from __future__ import annotations

from app.config import config
from app.rag.chunking import Chunk
from app.rag.embeddings import embedding_model

CUSTOMER_COLLECTION = "policies_customer"
STAFF_COLLECTION = "policies_staff"


def _flatten_meta(chunk: Chunk) -> dict:
    m = chunk.metadata or {}
    # Chroma metadata values must be scalars.
    return {
        "source": chunk.source,
        "source_file": chunk.source_file,
        "policy": str(m.get("policy", chunk.source)),
        "version": str(m.get("version", "1.0")),
        "region": str(m.get("region", config.DEFAULT_REGION)),
        "language": str(m.get("language", config.DEFAULT_LANGUAGE)),
        "effective_date": str(m.get("effective_date", "unknown")),
        "customer_safe": bool(m.get("customer_safe", True)),
    }


class VectorStore:
    def __init__(self, persist_dir: str | None = None, collection: str = CUSTOMER_COLLECTION) -> None:
        self._persist_dir = persist_dir or config.CHROMA_DIR
        self._collection_name = collection
        self._client = None
        self._collection = None

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name, metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def reset(self) -> None:
        collection = self._get_collection()
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        collection = self._get_collection()
        embeddings = embedding_model.encode([c.text for c in chunks])
        collection.add(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=embeddings,
            metadatas=[_flatten_meta(c) for c in chunks],
        )

    def count(self) -> int:
        return self._get_collection().count()

    def query(self, text: str, top_k: int = 20, where: dict | None = None) -> list[dict]:
        """Return up to ``top_k`` results, optionally filtered by metadata (R-4)."""
        collection = self._get_collection()
        if collection.count() == 0:
            return []
        query_emb = embedding_model.encode_one(text)
        kwargs = {
            "query_embeddings": [query_emb],
            "n_results": min(top_k, collection.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        res = collection.query(**kwargs)

        out: list[dict] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i in range(len(ids)):
            distance = dists[i] if i < len(dists) else 1.0
            similarity = max(0.0, 1.0 - float(distance))
            meta = metas[i] or {}
            out.append(
                {
                    "id": ids[i],
                    "text": docs[i],
                    "source": meta.get("source"),
                    "source_file": meta.get("source_file"),
                    "metadata": meta,
                    "score": similarity,
                }
            )
        return out


# Customer-facing singleton (the default the agent uses).
vector_store = VectorStore(collection=CUSTOMER_COLLECTION)
# Staff-only store (never served to customers).
staff_vector_store = VectorStore(collection=STAFF_COLLECTION)
