"""Embedding abstraction: local (offline) and hosted providers behind one interface.

``hash``                 Deterministic lexical hashing embedder. No downloads, works offline and in
                         tests. It matches on shared vocabulary, NOT meaning; use a model-based
                         provider below for true semantic retrieval.
``sentence_transformers`` Local model (optional dependency: ``pip install sentence-transformers``).
``openai_compatible``    Any OpenAI-style ``/embeddings`` endpoint (hosted).
``ollama``               Ollama's OpenAI-compatible endpoint (local).
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from app.config import settings


class EmbeddingError(Exception):
    pass


class EmbeddingProvider:
    name = "base"
    model = ""
    dimension = 0

    @property
    def identity(self) -> str:
        """Stable id used to namespace vector collections (different models never mix)."""
        raw = f"{self.name}:{self.model}"  # dimension is intentionally excluded (unknown until first call)
        return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_")[:40] + "_" + hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:8]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


_STOP = set("the and for with that this from have has are was were not but you your our out can all any "
            "its they them then than into onto over under about after before when what which will would "
            "should could been being also just only some such there their these those while where".split())
_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(tok: str) -> str:
    for suffix in ("ingly", "ing", "edly", "ed", "es", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            return tok[: -len(suffix)]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if len(t) > 2 and t not in _STOP]


class HashEmbedder(EmbeddingProvider):
    name = "hash"

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.model = f"hash-{dimension}"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = tokenize(text)
        counts: dict[str, float] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0.0) + 1.0
        for a, b in zip(tokens, tokens[1:], strict=False):  # bigrams add phrase sensitivity
            key = f"{a}_{b}"
            counts[key] = counts.get(key, 0.0) + 0.5
        for key, tf in counts.items():
            digest = hashlib.blake2b(key.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(tf))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts):
        return [self._vector(t) for t in texts]


class SentenceTransformerEmbedder(EmbeddingProvider):
    name = "sentence_transformers"

    def __init__(self, model: str = ""):
        self.model = model or "sentence-transformers/all-MiniLM-L6-v2"
        self._model = None
        self.dimension = 384 if "MiniLM" in self.model else 0

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError("sentence-transformers is not installed (pip install sentence-transformers)") from exc
            self._model = SentenceTransformer(self.model)
            self.dimension = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def embed(self, texts):
        return [list(map(float, v)) for v in self._load().encode(list(texts), normalize_embeddings=True)]


class OpenAICompatibleEmbedder(EmbeddingProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", name: str = "openai_compatible",
                 client=None, dimension: int = 0):
        if not base_url or not model:
            raise EmbeddingError(f"{name} embeddings need a base URL and model (EMBEDDING_BASE_URL/EMBEDDING_MODEL)")
        self.name, self.model, self.dimension = name, model, dimension
        self._base_url, self._api_key, self._client = base_url, api_key, client

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key or "local",
                                  timeout=settings.llm_timeout, max_retries=settings.llm_max_retries)
        return self._client

    def embed(self, texts):
        try:
            resp = self._get_client().embeddings.create(model=self.model, input=list(texts))
        except Exception as exc:  # noqa: BLE001
            from app.security.sanitize import mask_secrets

            raise EmbeddingError(mask_secrets(f"Embedding request failed: {type(exc).__name__}")) from exc
        vectors = [list(map(float, d.embedding)) for d in sorted(resp.data, key=lambda d: d.index)]
        if len(vectors) != len(texts):
            raise EmbeddingError("Embedding provider returned an unexpected number of vectors")
        if vectors:
            self.dimension = len(vectors[0])
        return vectors


def build_embedder() -> EmbeddingProvider:
    kind = settings.embedding_provider.lower()
    if kind == "hash":
        return HashEmbedder()
    if kind == "sentence_transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    if kind == "openai_compatible":
        return OpenAICompatibleEmbedder(settings.embedding_base_url, settings.embedding_model,
                                        settings.embedding_api_key.get_secret_value()
                                        or settings.openai_api_key.get_secret_value())
    if kind == "ollama":
        return OpenAICompatibleEmbedder(settings.embedding_base_url or settings.ollama_base_url,
                                        settings.embedding_model, "ollama", name="ollama")
    raise EmbeddingError(f"Unsupported EMBEDDING_PROVIDER: {kind}")
