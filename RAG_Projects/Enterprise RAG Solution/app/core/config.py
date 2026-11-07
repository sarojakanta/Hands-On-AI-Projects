"""
Central application configuration.

All settings come from environment variables or a ``.env`` file located in the
project root (see ``.env.example``). API keys are held as ``SecretStr`` so they
never appear in ``repr()``, logs or API responses.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

BUILTIN_PROVIDER_NAMES = frozenset({"groq", "openai", "anthropic", "ollama"})
_PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def _clean_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")
    return value


class CustomProviderConfig(BaseModel):
    """
    An additional OpenAI-compatible provider (Together, Mistral, vLLM, LM Studio, ...).

    The API key itself is never stored here: ``api_key_env`` names the environment
    variable (or ``.env`` entry) that holds it.
    """

    name: str
    base_url: str
    api_key_env: str | None = None
    default_model: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not _PROVIDER_NAME_RE.match(value):
            raise ValueError("name must match [a-z][a-z0-9_-]{1,31}")
        if value in BUILTIN_PROVIDER_NAMES:
            raise ValueError(f"'{value}' is a built-in provider name")
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return _clean_base_url(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- General ---------------------------------------------------------
    log_level: str = "INFO"

    # ---- LLM generation defaults ----------------------------------------
    llm_default_provider: str = "ollama"
    llm_default_model: str = ""          # empty -> the provider's own default model
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1024, ge=1, le=32768)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=6)

    # ---- Optional provider fallback -------------------------------------
    llm_fallback_enabled: bool = False
    llm_fallback_provider: str = ""
    llm_fallback_model: str = ""

    # ---- Groq ------------------------------------------------------------
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_default_model: str = ""

    # ---- OpenAI ----------------------------------------------------------
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = ""

    # ---- Anthropic Claude -----------------------------------------------
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_default_model: str = ""
    anthropic_version: str = "2023-06-01"

    # ---- Ollama (local, no API key) --------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = ""

    # ---- Other OpenAI-compatible providers (JSON list in the environment) -
    openai_compatible_providers: list[CustomProviderConfig] = Field(default_factory=list)

    # ---- RAG answer generation -------------------------------------------
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_min_score: float = Field(default=0.25, ge=0.0, le=1.0)
    rag_max_context_chars: int = Field(default=6000, ge=500, le=30000)
    rag_history_turns: int = Field(default=3, ge=0, le=10)
    rag_rewrite_enabled: bool = True
    rag_max_question_chars: int = Field(default=2000, ge=50, le=8000)
    rag_max_conversations: int = Field(default=200, ge=1, le=10000)

    # ---- Retrieval quality -------------------------------------------------
    rag_retrieval_mode: Literal["vector", "hybrid"] = "hybrid"          # vector | hybrid (BM25 keyword search fused with vector search)
    rag_candidate_multiplier: int = Field(default=4, ge=1, le=20)   # candidates fetched per source = top_k * this
    rag_rrf_k: int = Field(default=60, ge=1, le=1000)               # reciprocal rank fusion constant
    rag_lexical_min_coverage: float = Field(default=0.6, ge=0.0, le=1.0)   # keyword evidence that can admit a chunk
    rag_reranker: Literal["none", "cross-encoder"] = "none"                  # none | cross-encoder
    rag_reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ---- Operations ---------------------------------------------------------
    metrics_enabled: bool = True                 # expose Prometheus metrics at /metrics
    rate_limit_enabled: bool = True
    rate_limit_llm_per_minute: int = Field(default=30, ge=0, le=100000)     # chat / completions per client; 0 = unlimited
    rate_limit_write_per_minute: int = Field(default=30, ge=0, le=100000)   # uploads, deletes, reindex, config changes
    trust_proxy_headers: bool = False            # read the client address from X-Forwarded-For (only behind your own proxy)

    # ---- Storage -----------------------------------------------------------
    # Relative paths are resolved against the project root, not the working directory.
    data_dir: Path = Path("data")
    database_url: str = ""                # empty -> SQLite file inside data_dir
    upload_dir: Path | None = None        # empty -> <data_dir>/uploads
    vector_store: str = "chroma"          # chroma | pgvector
    chroma_path: Path | None = None       # empty -> <data_dir>/chroma
    vector_store_url: str = ""            # pgvector: SQLAlchemy URL (empty -> database_url when it is PostgreSQL)
    seed_sample_docs: bool = True         # on an EMPTY database, ingest the files in seed_dir once
    seed_dir: Path = Path("sample_docs")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)

    # ---- Embeddings ----------------------------------------------------------
    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # ---- Chunking --------------------------------------------------------------
    chunk_size: int = Field(default=300, ge=100, le=4000)
    chunk_overlap: int = Field(default=50, ge=0, le=1000)
    chunk_strategy: Literal["sentence", "recursive", "structure"] = "sentence"

    @field_validator("groq_base_url", "openai_base_url", "anthropic_base_url", "ollama_base_url")
    @classmethod
    def _validate_urls(cls, value: str) -> str:
        return _clean_base_url(value)

    # A blank value (as in .env.example) must mean "use the default", never "the current directory".
    @field_validator("upload_dir", "chroma_path", mode="before")
    @classmethod
    def _blank_optional_path(cls, value):
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _blank_data_dir(cls, value):
        return "data" if isinstance(value, str) and not value.strip() else value

    @field_validator("seed_dir", mode="before")
    @classmethod
    def _blank_seed_dir(cls, value):
        return "sample_docs" if isinstance(value, str) and not value.strip() else value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid log level")
        return value

    @model_validator(mode="after")
    def _check_custom_provider_names_unique(self) -> "Settings":
        names = [p.name for p in self.openai_compatible_providers]
        if len(names) != len(set(names)):
            raise ValueError("openai_compatible_providers contains duplicate names")
        return self

    @model_validator(mode="after")
    def _check_storage_settings(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.vector_store.strip().lower() not in {"chroma", "pgvector"}:
            raise ValueError("vector_store must be 'chroma' or 'pgvector'")
        return self

    # ---- resolved storage locations ------------------------------------------

    @staticmethod
    def _absolute(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def data_path(self) -> Path:
        return self._absolute(self.data_dir)

    @property
    def upload_path(self) -> Path:
        return self._absolute(self.upload_dir) if self.upload_dir else self.data_path / "uploads"

    @property
    def chroma_dir(self) -> Path:
        return self._absolute(self.chroma_path) if self.chroma_path else self.data_path / "chroma"

    @property
    def seed_path(self) -> Path:
        return self._absolute(self.seed_dir)

    @property
    def database_dsn(self) -> str:
        """SQLAlchemy URL of the metadata database (SQLite file by default)."""
        return self.database_url.strip() or f"sqlite:///{(self.data_path / 'app.db').as_posix()}"

    def resolve_custom_api_key(self, config: CustomProviderConfig) -> SecretStr | None:
        """Look up a custom provider's key from the environment, then from ``.env``."""
        if not config.api_key_env:
            return None
        value = os.environ.get(config.api_key_env)
        if not value and ENV_FILE.exists():
            value = dotenv_values(ENV_FILE).get(config.api_key_env)
        return SecretStr(value) if value else None


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings instance (cached)."""
    return Settings()
