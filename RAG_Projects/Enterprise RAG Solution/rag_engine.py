"""
RAG Engine - Core logic for document loading, chunking, embedding, and retrieval.
Uses sentence-transformers for embeddings and FAISS for vector storage.
No external LLM API needed — answers are composed from retrieved chunks directly.
"""

import os
import re
import numpy as np
from pathlib import Path
from typing import List, Tuple, Dict

# Lazy imports so startup errors are clear
try:
    from sentence_transformers import SentenceTransformer
    import faiss
except ImportError as e:
    raise ImportError(f"Missing dependency: {e}. Run: pip install sentence-transformers faiss-cpu")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DOCS_DIR = Path(__file__).parent / "sample_docs"
CHUNK_SIZE = 300          # characters per chunk (approximate)
CHUNK_OVERLAP = 50        # overlap between consecutive chunks
TOP_K = 5                 # number of chunks to retrieve
EMBED_MODEL = "all-MiniLM-L6-v2"   # small, fast, good quality


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------
# The chunking implementation now lives in app.ingestion.chunking (shared with the new ingestion pipeline).
from app.ingestion.chunking import chunk_text  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Document store (in-memory)
# ---------------------------------------------------------------------------
class RAGEngine:
    def __init__(self):
        self.model: SentenceTransformer | None = None
        self.index: faiss.IndexFlatIP | None = None   # Inner-product (cosine with normalized vecs)
        self.chunks: List[str] = []
        self.metadata: List[Dict] = []   # source file, chunk index
        self.is_ready = False

    # ---- Build ----

    def load_documents(self, docs_dir: Path = DOCS_DIR) -> List[Tuple[str, str]]:
        """Load all .txt files from docs_dir. Returns list of (filename, content)."""
        docs = []
        for path in sorted(docs_dir.glob("*.txt")):
            docs.append((path.name, path.read_text(encoding="utf-8")))
        return docs

    def build_index(self, docs_dir: Path = DOCS_DIR):
        """Load docs → chunk → embed → build FAISS index."""
        print("Loading embedding model …")
        self.model = SentenceTransformer(EMBED_MODEL)

        docs = self.load_documents(docs_dir)
        if not docs:
            raise FileNotFoundError(f"No .txt files found in {docs_dir}")

        print(f"Loaded {len(docs)} document(s). Chunking …")
        self.chunks = []
        self.metadata = []

        for filename, content in docs:
            doc_chunks = chunk_text(content)
            for i, chunk in enumerate(doc_chunks):
                self.chunks.append(chunk)
                self.metadata.append({"source": filename, "chunk_index": i})

        print(f"Total chunks: {len(self.chunks)}. Embedding …")
        embeddings = self.model.encode(self.chunks, normalize_embeddings=True, show_progress_bar=True)
        embeddings = np.array(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)   # cosine similarity via dot product on normalized vecs
        self.index.add(embeddings)

        self.is_ready = True
        print("FAISS index built. RAG engine ready.")

    # ---- Query ----

    def retrieve(self, query: str, top_k: int = TOP_K) -> List[Dict]:
        """Return top-k most relevant chunks for a query."""
        if not self.is_ready:
            raise RuntimeError("Index not built. Call build_index() first.")

        q_emb = self.model.encode([query], normalize_embeddings=True)
        q_emb = np.array(q_emb, dtype="float32")

        scores, indices = self.index.search(q_emb, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            results.append({
                "chunk": self.chunks[idx],
                "source": self.metadata[idx]["source"],
                "chunk_index": self.metadata[idx]["chunk_index"],   # position of the chunk inside its document
                "score": float(score),
            })
        return results

    def answer(self, query: str, top_k: int = TOP_K) -> Dict:
        """
        Retrieve relevant chunks and compose an answer.
        Since we're not calling an external LLM, the answer IS the retrieved
        context (presented clearly), plus a confidence note.
        """
        results = self.retrieve(query, top_k)

        if not results:
            return {
                "answer": "I couldn't find relevant information for your query.",
                "sources": [],
                "chunks": [],
            }

        # Build a readable answer from the top chunks
        top = results[0]
        answer_text = (
            f"{top['chunk']}"
        )

        sources = list({r["source"] for r in results})

        return {
            "answer": answer_text,
            "sources": sources,
            "chunks": results,
            "query": query,
        }

    def get_document_list(self) -> List[Dict]:
        """Return info about loaded documents."""
        seen = {}
        for meta in self.metadata:
            src = meta["source"]
            if src not in seen:
                seen[src] = 0
            seen[src] += 1
        return [{"filename": k, "chunks": v} for k, v in seen.items()]


# ---------------------------------------------------------------------------
# Singleton engine instance (shared across requests)
# ---------------------------------------------------------------------------
engine = RAGEngine()
