"""Environment-driven application configuration.

All settings are read from environment variables (optionally loaded from a
``.env`` file). Nothing sensitive is hardcoded here. Each setting has a
sensible default so the app can boot for local development out of the box.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = two levels up from this file (app/config.py -> app -> root)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present (does not override already-set environment variables).
load_dotenv(BASE_DIR / ".env")

# Silence ChromaDB's telemetry stub (it logs noisy warnings even when disabled).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def _sanitize_env_urls() -> None:
    """Scrub *_BASE_URL env vars of leaked inline comments / invalid values.

    Some SDKs (notably the OpenAI client) read these env vars DIRECTLY, so a
    malformed value from a .env inline comment would bypass our own config
    handling. If a URL does not start with http(s), clear it so defaults apply.
    """
    for key in ("OPENAI_BASE_URL", "GROQ_BASE_URL", "ANTHROPIC_BASE_URL", "OPENROUTER_BASE_URL", "OPENAI_API_BASE"):
        val = os.environ.get(key)
        if val is None:
            continue
        v = val.strip()
        if not v or not (v.startswith("http://") or v.startswith("https://")):
            # Remove it entirely so the SDK falls back to its own default.
            os.environ.pop(key, None)


_sanitize_env_urls()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        return default


def _resolve(path_str: str) -> str:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


class Config:
    """Central config object. Instantiate with ``Config()``."""

    # ---- Flask ----
    ENV = os.getenv("FLASK_ENV", "development")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = _int(os.getenv("PORT"), 5000)
    DEV_MODE = _bool(os.getenv("DEV_MODE"), True)

    # ---- LLM provider selection ----
    # auto | openai | groq | anthropic | openrouter. "auto" picks the first configured
    # provider in priority order (groq -> openai -> anthropic -> openrouter).
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").strip().lower() or "auto"

    # ---- OpenAI (direct) ----
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # Blank -> SDK/native default (api.openai.com).
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")

    # ---- Groq (direct) ----
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    # Blank -> code default (https://api.groq.com/openai/v1).
    GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "")

    # ---- Anthropic (direct) ----
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    # Blank -> code default (https://api.anthropic.com/v1).
    ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "")

    # ---- OpenRouter (gateway) ----
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    # Blank -> code default (https://openrouter.ai/api/v1).
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
    OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "AI Customer Support Agent")
    OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")

    # ---- Ollama (local, explicit-only; NOT part of LLM_PROVIDER=auto) ----
    # Requires Ollama running locally with the model pulled. No real key needed;
    # a dummy is used because the OpenAI SDK requires a non-empty api_key.
    OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
    # Blank -> code default (http://localhost:11434/v1).
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "")

    # ---- Database ----
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/support_agent.db")

    # ---- RAG ----
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    CHROMA_DIR = _resolve(os.getenv("CHROMA_DIR", "data/chroma"))
    KNOWLEDGE_BASE_DIR = _resolve(os.getenv("KNOWLEDGE_BASE_DIR", "knowledge_base"))
    # Token-based chunking (R-1): 300-500 tokens with a small overlap.
    CHUNK_TOKENS = _int(os.getenv("CHUNK_TOKENS"), 400)
    CHUNK_OVERLAP_TOKENS = _int(os.getenv("CHUNK_OVERLAP_TOKENS"), 60)
    # Legacy char-based settings (kept for backward compat / fallback).
    CHUNK_SIZE = _int(os.getenv("CHUNK_SIZE"), 800)
    CHUNK_OVERLAP = _int(os.getenv("CHUNK_OVERLAP"), 120)
    HYBRID_VECTOR_WEIGHT = _float(os.getenv("HYBRID_VECTOR_WEIGHT"), 0.5)
    RETRIEVAL_TOP_K = _int(os.getenv("RETRIEVAL_TOP_K"), 4)
    # R-3: fetch a larger candidate pool, rerank, then keep the best K.
    RETRIEVAL_CANDIDATES = _int(os.getenv("RETRIEVAL_CANDIDATES"), 20)
    RERANK_ENABLED = _bool(os.getenv("RERANK_ENABLED"), True)
    RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    # R-5: minimum rerank score for a passage to count as supporting.
    RERANK_MIN_SCORE = _float(os.getenv("RERANK_MIN_SCORE"), 0.0)
    # R-4: default customer region/language used to filter retrieval.
    DEFAULT_REGION = os.getenv("DEFAULT_REGION", "IN")
    DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en")
    # R-6: re-index if the KB changed and this interval has elapsed.
    REINDEX_INTERVAL_SECONDS = _int(os.getenv("REINDEX_INTERVAL_SECONDS"), 900)
    # R-7: staff-only knowledge base directory (never served to customers).
    STAFF_KB_DIR = _resolve(os.getenv("STAFF_KB_DIR", "knowledge_base_staff"))
    # Team-knowledge (approved edge-case answers by support leads, monthly
    # review). Customer-safe; merged into the customer index.
    TEAM_KB_DIR = _resolve(os.getenv("TEAM_KB_DIR", "knowledge_base_team"))

    # ---- Memory ----
    REDIS_URL = os.getenv("REDIS_URL", "")
    SESSION_TTL_SECONDS = _int(os.getenv("SESSION_TTL_SECONDS"), 1800)

    # ---- Authentication / OTP ----
    DEV_OTP = os.getenv("DEV_OTP", "123456")
    OTP_TTL_SECONDS = _int(os.getenv("OTP_TTL_SECONDS"), 300)
    OTP_MAX_ATTEMPTS = _int(os.getenv("OTP_MAX_ATTEMPTS"), 3)

    # ---- MCP server ----
    MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    MCP_SERVER_PORT = _int(os.getenv("MCP_SERVER_PORT"), 8765)

    # ---- Ticket provider ----
    # Where support tickets are created/stored: "local" (SQLite, default) or
    # "salesforce" (creates a Case in a real org). Local keeps the demo
    # self-contained; Salesforce is an opt-in integration.
    TICKET_PROVIDER = os.getenv("TICKET_PROVIDER", "local").strip().lower() or "local"
    # When using Salesforce and it fails, fall back to local so tickets are
    # never silently lost.
    TICKET_FALLBACK_LOCAL = _bool(os.getenv("TICKET_FALLBACK_LOCAL"), True)
    # In dev mode with local ticketing, retain at most this many tickets by
    # pruning the oldest resolved/closed tickets. Open tickets are never pruned.
    DEV_TICKET_MAX_COUNT = _int(os.getenv("DEV_TICKET_MAX_COUNT"), 50)

    # ---- Salesforce (opt-in; OAuth username-password flow) ----
    SALESFORCE_USERNAME = os.getenv("SALESFORCE_USERNAME", "")
    SALESFORCE_PASSWORD = os.getenv("SALESFORCE_PASSWORD", "")
    SALESFORCE_SECURITY_TOKEN = os.getenv("SALESFORCE_SECURITY_TOKEN", "")
    SALESFORCE_CONSUMER_KEY = os.getenv("SALESFORCE_CONSUMER_KEY", "")
    SALESFORCE_CONSUMER_SECRET = os.getenv("SALESFORCE_CONSUMER_SECRET", "")
    # "login" for production, "test" for sandbox.
    SALESFORCE_DOMAIN = os.getenv("SALESFORCE_DOMAIN", "login")

    # ---- Tool safety (T-3, T-6) ----
    TOOL_TIMEOUT_SECONDS = _int(os.getenv("TOOL_TIMEOUT_SECONDS"), 5)
    TOOL_MAX_RETRIES = _int(os.getenv("TOOL_MAX_RETRIES"), 1)
    MAX_TOOL_CALLS_PER_CONVERSATION = _int(os.getenv("MAX_TOOL_CALLS_PER_CONVERSATION"), 8)
    MAX_OTP_REQUESTS_PER_HOUR = _int(os.getenv("MAX_OTP_REQUESTS_PER_HOUR"), 5)

    # ---- Evaluation / rollout (E-4) ----
    # Shadow mode: the agent DRAFTS a reply but a human sends it. When on, the
    # chat API returns the draft flagged as shadow and logs it for comparison.
    SHADOW_MODE = _bool(os.getenv("SHADOW_MODE"), False)

    # ---- Logging ----
    LOG_DIR = _resolve(os.getenv("LOG_DIR", "logs"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    # Per-log-file size and rotated backup count; rotation is dev-mode only.
    LOG_MAX_MB = _int(os.getenv("LOG_MAX_MB"), 80)
    LOG_BACKUP_COUNT = _int(os.getenv("LOG_BACKUP_COUNT"), 3)

    # ---- Derived paths ----
    DATA_DIR = str(BASE_DIR / "data")

    @property
    def sqlalchemy_url(self) -> str:
        """Return an absolute SQLite URL so the DB path is stable regardless
        of the current working directory."""
        url = self.DATABASE_URL
        prefix = "sqlite:///"
        if url.startswith(prefix):
            rel = url[len(prefix):]
            abs_path = _resolve(rel)
            # Normalise Windows backslashes for SQLAlchemy URL.
            return prefix + abs_path.replace("\\", "/")
        return url

    def ensure_dirs(self) -> None:
        """Create runtime directories that must exist before use."""
        for d in (self.DATA_DIR, self.LOG_DIR, self.CHROMA_DIR):
            Path(d).mkdir(parents=True, exist_ok=True)


# A module-level singleton for convenient import.
config = Config()
