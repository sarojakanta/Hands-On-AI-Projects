"""Centralised, environment-driven application settings.

All secrets and tunables are read from environment variables / a `.env`
file. Nothing sensitive is hard-coded. Access settings via
``get_settings()`` which is cached for the process lifetime.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "BizAssist"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me"

    # --- Database ---
    database_url: str = "sqlite:///./bizassist.db"

    # --- Auth ---
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    # --- LLM: provider selection ---
    # auto | openai | groq | anthropic | openrouter | ollama | mock
    # "auto" picks the first provider with a configured key
    # (groq -> openai -> anthropic -> openrouter); ollama is explicit-only;
    # if none is configured it falls back to the mock. Default: ollama (local).
    llm_provider: str = "ollama"
    llm_temperature: float = 0.2
    # Explicit kill-switch: force the deterministic mock regardless of provider.
    use_mock_llm: bool = False

    # OpenAI (direct)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    # Groq (OpenAI-compatible)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = ""

    # Anthropic Claude (native SDK)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    anthropic_base_url: str = ""

    # OpenRouter (OpenAI-compatible gateway)
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = ""
    openrouter_app_name: str = "BizAssist"
    openrouter_site_url: str = "http://localhost:8000"

    # Ollama (local, OpenAI-compatible; no real key needed)
    ollama_api_key: str = "ollama"
    ollama_model: str = "llama3.1"
    ollama_base_url: str = ""

    # Embeddings: follow the chat provider when it supports embeddings
    # (openai / ollama), otherwise use the local hashing embedder.
    # Values: auto | openai | ollama | hashing
    embedding_provider: str = "auto"
    ollama_embedding_model: str = "nomic-embed-text"

    # --- RAG ---
    chroma_persist_dir: str = "./chroma_store"
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_top_k: int = 4
    # Max characters of retrieved context passed to the LLM as grounding.
    rag_context_max_chars: int = 2400

    # --- Chat limits ---
    # Max length of a single student chat message (longer -> 422).
    max_chat_message_chars: int = 2000
    # How many prior turns are fed back to the LLM for context.
    chat_history_turns: int = 8

    # --- Storage ---
    knowledge_base_dir: str = "./knowledge_base"

    # --- Rate limiting ---
    public_chat_rate_limit: str = "20/minute"

    # --- CORS ---
    # Comma-separated list of allowed origins for the widget. "*" allows any
    # (fine for local/demo; set explicit origins in production).
    cors_allow_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if raw in ("", "*"):
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    # Priority order used by LLM_PROVIDER=auto (ollama is intentionally excluded).
    _AUTO_ORDER = ("groq", "openai", "anthropic", "openrouter")

    def _provider_has_key(self, provider: str) -> bool:
        """True if the given provider has the credentials it needs."""
        if provider == "ollama":
            return True  # local; no real key required
        key = getattr(self, f"{provider}_api_key", "")
        return bool(key)

    def resolve_llm_provider(self) -> str:
        """Return the effective provider name after applying overrides.

        Resolution:
          1. use_mock_llm=true          -> "mock"
          2. llm_provider == "mock"      -> "mock"
          3. llm_provider == "auto"      -> first of groq/openai/anthropic/
                                            openrouter with a key, else "mock"
          4. explicit provider           -> that provider if usable, else "mock"
        """
        if self.use_mock_llm:
            return "mock"
        provider = (self.llm_provider or "auto").strip().lower()
        if provider == "mock":
            return "mock"
        if provider == "auto":
            for candidate in self._AUTO_ORDER:
                if self._provider_has_key(candidate):
                    return candidate
            return "mock"
        # Explicit provider: use it if it has what it needs, else mock.
        if provider in {"openai", "groq", "anthropic", "openrouter", "ollama"}:
            return provider if self._provider_has_key(provider) else "mock"
        return "mock"

    def resolve_embedding_provider(self) -> str:
        """Return the effective embedding provider: openai | ollama | hashing."""
        pref = (self.embedding_provider or "auto").strip().lower()
        if pref in {"openai", "ollama", "hashing"}:
            if pref == "openai":
                return "openai" if self.openai_api_key else "hashing"
            return pref
        # auto: follow the chat provider if it can embed, else hashing.
        chat = self.resolve_llm_provider()
        if chat == "openai" and self.openai_api_key:
            return "openai"
        if chat == "ollama":
            return "ollama"
        return "hashing"

    @property
    def llm_enabled(self) -> bool:
        """True when a real (non-mock) LLM provider is active."""
        return self.resolve_llm_provider() != "mock"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
