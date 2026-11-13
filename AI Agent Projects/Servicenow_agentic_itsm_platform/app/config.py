import os
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration. All values come from environment variables or .env.

    Secrets use SecretStr so they never appear in repr()/logs/dashboards.
    """

    app_env: str = "development"  # development | test | production
    log_level: str = "INFO"
    log_json: bool = False

    # --- Persistence ---
    database_url: str = "sqlite:///./data/itsm.db"
    auto_create_tables: bool = True  # dev/test convenience; use Alembic in production

    # --- Security ---
    jwt_secret: SecretStr = SecretStr("change-me-in-production-change-me-32b")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    bcrypt_rounds: int = 12
    auth_enabled: bool = True
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: SecretStr = SecretStr("")  # empty => no bootstrap user

    # --- ServiceNow ---
    servicenow_mode: str = "mock"  # mock | live
    servicenow_instance: str = ""
    servicenow_auth: str = "basic"  # basic | oauth
    servicenow_username: str = ""
    servicenow_password: SecretStr = SecretStr("")
    servicenow_client_id: str = ""
    servicenow_client_secret: SecretStr = SecretStr("")
    servicenow_timeout: int = 20
    servicenow_max_retries: int = 3
    servicenow_page_size: int = 100
    servicenow_max_records: int = 500
    servicenow_write_enabled: bool = False  # read-only unless explicitly enabled
    servicenow_field_map_file: str = ""  # optional JSON overriding field/table mappings

    # --- LLM ---
    llm_provider: str = "mock"  # mock | groq | ollama | openai_compatible
    llm_fallback_providers: str = ""  # comma separated, tried in order on failure
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1"
    openai_api_key: SecretStr = SecretStr("")
    llm_timeout: float = 30.0
    llm_max_retries: int = 2
    llm_cost_per_1k_input: float = 0.0
    llm_cost_per_1k_output: float = 0.0

    # --- Knowledge / RAG ---
    knowledge_dir: str = "data/knowledge"
    chroma_dir: str = "data/chroma"
    embedding_provider: str = "hash"  # hash | sentence_transformers | openai_compatible
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    chunk_size: int = 800
    chunk_overlap: int = 100
    # Cosine floor for knowledge excerpts. 0.10 suits the lexical hash embedder; semantic
    # embedders (ollama/sentence_transformers/hosted) score higher, so raise it to ~0.3-0.5 with them.
    knowledge_min_score: float = 0.10
    similar_min_score: float = 0.2     # floor for similar historical incidents

    # --- Approvals ---
    approval_ttl_hours: int = 72
    # None => allowed outside production, forbidden in production (separation of duties).
    approval_allow_self_approval: bool | None = None

    # --- Orchestration ---
    max_workflow_steps: int = 20
    max_agent_retries: int = 2

    # ITSM_ENV_FILE lets tests (or special deployments) point away from the default .env.
    # env_ignore_empty: a blank `KEY=` in .env means "use the default" instead of failing to parse.
    model_config = SettingsConfigDict(env_file=os.getenv("ITSM_ENV_FILE", ".env"), extra="ignore",
                                      env_ignore_empty=True)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def safe_dict(self) -> dict:
        """Configuration with every secret masked; safe for dashboards and logs."""
        from app.security.sanitize import mask_secrets  # local import: sanitize reads settings

        out = {}
        for key, value in self.model_dump().items():
            if isinstance(value, SecretStr):
                out[key] = "***set***" if value.get_secret_value() else ""
            elif isinstance(value, str):
                out[key] = mask_secrets(value)  # also strips credentials embedded in URLs
            else:
                out[key] = value
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
