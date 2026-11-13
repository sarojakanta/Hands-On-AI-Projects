"""Hermetic test environment.

Tests must never depend on, or be influenced by, a developer's .env file or shell environment
(and must never reach a real ServiceNow/LLM). Configuration is pinned BEFORE the app is imported.
"""
import os

_PREFIXES = ("SERVICENOW_", "LLM_", "GROQ_", "OLLAMA_", "OPENAI_", "EMBEDDING_", "KNOWLEDGE_", "CHROMA_", "DATABASE_",
             "AUTO_CREATE_", "JWT_", "BCRYPT_", "AUTH_", "BOOTSTRAP_", "APPROVAL_", "APP_ENV", "LOG_", "MAX_",
             "CHUNK_", "SIMILAR_")
for _key in [k for k in os.environ if k.startswith(_PREFIXES)]:
    del os.environ[_key]
os.environ.update({
    "ITSM_ENV_FILE": os.path.join(os.path.dirname(__file__), "no-such.env"),  # ignore any developer .env
    "APP_ENV": "test", "SERVICENOW_MODE": "mock", "LLM_PROVIDER": "mock", "EMBEDDING_PROVIDER": "hash",
    "DATABASE_URL": "sqlite://", "JWT_SECRET": "change-me-in-production-change-me-32b",
})

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.base import configure_database  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db():
    """Every test gets a fresh in-memory database; no external services needed."""
    engine = configure_database("sqlite://", create_tables=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(settings, "bcrypt_rounds", 4)  # bcrypt minimum; keeps auth tests fast


@pytest.fixture(autouse=True)
def fresh_services():
    """Service singletons (mock ServiceNow state, retriever) and login throttling reset per test."""
    from app.api.deps import get_services
    from app.security.auth import reset_throttle

    get_services.cache_clear()
    reset_throttle()
    yield
    get_services.cache_clear()


@pytest.fixture(autouse=True)
def isolated_vector_store(tmp_path, monkeypatch):
    """Each test gets its own ChromaDB directory and the offline hash embedder."""
    monkeypatch.setattr(settings, "chroma_dir", str(tmp_path / "chroma"))
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    yield
