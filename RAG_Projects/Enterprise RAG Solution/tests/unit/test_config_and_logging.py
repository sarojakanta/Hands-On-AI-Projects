"""Unit tests for settings parsing, secret handling, error envelope and log redaction."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, CustomProviderConfig, Settings
from app.core.errors import AppError
from app.core.logging import REDACTED, RedactingFilter, redact


def _settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_defaults_are_safe():
    s = _settings()
    assert s.llm_default_provider == "ollama"
    assert s.groq_api_key is None
    assert s.llm_fallback_enabled is False
    assert s.ollama_base_url == "http://localhost:11434"


def test_env_example_parses_cleanly():
    """The shipped template must always be loadable (blank values included)."""
    s = Settings(_env_file=PROJECT_ROOT / ".env.example")
    assert s.openai_compatible_providers == []
    assert s.llm_max_retries == 2


def test_blank_storage_values_use_safe_defaults_not_the_current_directory():
    s = Settings(_env_file=PROJECT_ROOT / ".env.example")   # ships with blank UPLOAD_DIR / CHROMA_PATH / DATABASE_URL
    assert s.upload_path == PROJECT_ROOT / "data" / "uploads"
    assert s.chroma_dir == PROJECT_ROOT / "data" / "chroma"
    assert s.database_dsn.startswith("sqlite:///") and s.database_dsn.endswith("data/app.db")
    assert s.seed_path == PROJECT_ROOT / "sample_docs"
    blank = _settings(data_dir="  ", seed_dir="")
    assert blank.data_path == PROJECT_ROOT / "data" and blank.seed_path == PROJECT_ROOT / "sample_docs"


def test_storage_validation():
    with pytest.raises(ValidationError):
        _settings(chunk_size=200, chunk_overlap=200)
    with pytest.raises(ValidationError):
        _settings(vector_store="faiss")
    assert _settings(vector_store=" PgVector ").vector_store.strip().lower() == "pgvector"
    assert _settings(database_url="postgresql+psycopg://u:p@h/db").database_dsn.startswith("postgresql")


def test_base_urls_are_normalised_and_validated():
    assert _settings(groq_base_url="https://example.com/v1/ ").groq_base_url == "https://example.com/v1"
    with pytest.raises(ValidationError):
        _settings(ollama_base_url="localhost:11434")


def test_numeric_bounds_are_enforced():
    with pytest.raises(ValidationError):
        _settings(llm_temperature=5)
    with pytest.raises(ValidationError):
        _settings(llm_max_tokens=0)


def test_api_keys_never_appear_in_repr_or_dump():
    s = _settings(openai_api_key="sk-supersecretvalue123")
    assert "supersecretvalue123" not in repr(s)
    assert "supersecretvalue123" not in str(s.model_dump())
    assert s.openai_api_key.get_secret_value() == "sk-supersecretvalue123"


def test_custom_providers_parse_from_json_env(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_COMPATIBLE_PROVIDERS",
        '[{"name":"Together","base_url":"https://api.together.xyz/v1/","api_key_env":"TOGETHER_API_KEY"}]',
    )
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-key-12345678")
    s = _settings()
    (provider,) = s.openai_compatible_providers
    assert provider.name == "together"
    assert provider.base_url == "https://api.together.xyz/v1"
    assert s.resolve_custom_api_key(provider).get_secret_value() == "tg-key-12345678"


def test_custom_provider_without_key_env_has_no_key():
    cfg = CustomProviderConfig(name="local-vllm", base_url="http://localhost:8001/v1")
    assert _settings().resolve_custom_api_key(cfg) is None


@pytest.mark.parametrize("name", ["groq", "OpenAI", "ollama", "anthropic", "x", "bad name", "1abc"])
def test_custom_provider_names_are_validated(name):
    with pytest.raises(ValidationError):
        CustomProviderConfig(name=name, base_url="http://localhost:1/v1")


def test_duplicate_custom_provider_names_rejected():
    providers = [
        {"name": "dup", "base_url": "http://a/v1"},
        {"name": "dup", "base_url": "http://b/v1"},
    ]
    with pytest.raises(ValidationError):
        _settings(openai_compatible_providers=providers)


def test_app_error_payload_shape():
    err = AppError("nope", code="custom_code", status_code=418, details={"hint": "x"})
    assert err.to_payload("req-1") == {
        "error": {"code": "custom_code", "message": "nope", "details": {"hint": "x"}, "request_id": "req-1"}
    }


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc.def-123_456",
        'headers={"x-api-key": "sk-ant-abcdefgh12345678"}',
        "using key sk-abcdefghijklmnop for request",
        "groq gsk_abcdefghijklmnop1234 failed",
        "api_key=topsecretvalue&foo=bar",
    ],
)
def test_redact_removes_credentials(text):
    cleaned = redact(text)
    assert REDACTED in cleaned
    for leaked in ("abc.def-123_456", "abcdefgh12345678", "abcdefghijklmnop", "topsecretvalue"):
        assert leaked not in cleaned


def test_redacting_filter_scrubs_formatted_message_and_args():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "key=%s", ("api_key=sk-abcdefghijklmnop",), None)
    assert RedactingFilter().filter(record) is True
    assert "abcdefghijklmnop" not in record.getMessage()
