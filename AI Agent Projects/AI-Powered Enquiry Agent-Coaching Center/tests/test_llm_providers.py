"""Tests for LLM provider resolution and client construction.

No network calls — we only check the selection logic and that the right
client type is built for each provider.
"""
from __future__ import annotations

from app.agents.llm_client import (
    AnthropicClient,
    MockLLMClient,
    OpenAICompatibleClient,
    _build_client,
)
from app.config.settings import Settings


def _settings(**overrides) -> Settings:
    # _env_file=None ignores the local .env. The test process sets
    # USE_MOCK_LLM=true in the environment (see conftest); override it to
    # False by default so we can exercise real provider-resolution logic.
    overrides.setdefault("use_mock_llm", False)
    overrides.setdefault("llm_provider", "ollama")
    return Settings(_env_file=None, **overrides)


# --- resolve_llm_provider -------------------------------------------------
def test_default_provider_is_ollama():
    # The class-level default for the provider field is ollama.
    assert Settings.model_fields["llm_provider"].default == "ollama"
    # And it resolves to ollama when mock isn't forced.
    assert _settings().resolve_llm_provider() == "ollama"


def test_use_mock_overrides_everything():
    s = _settings(use_mock_llm=True, llm_provider="ollama", anthropic_api_key="x")
    assert s.resolve_llm_provider() == "mock"


def test_explicit_mock():
    assert _settings(llm_provider="mock").resolve_llm_provider() == "mock"


def test_auto_prefers_groq_then_openai():
    s = _settings(llm_provider="auto", groq_api_key="g", openai_api_key="o")
    assert s.resolve_llm_provider() == "groq"
    s2 = _settings(llm_provider="auto", openai_api_key="o", anthropic_api_key="a")
    assert s2.resolve_llm_provider() == "openai"


def test_auto_falls_back_to_mock_when_no_keys():
    assert _settings(llm_provider="auto").resolve_llm_provider() == "mock"


def test_explicit_provider_without_key_falls_back_to_mock():
    assert _settings(llm_provider="anthropic").resolve_llm_provider() == "mock"
    assert _settings(llm_provider="openai").resolve_llm_provider() == "mock"


def test_explicit_provider_with_key_is_used():
    s = _settings(llm_provider="anthropic", anthropic_api_key="sk-ant-x")
    assert s.resolve_llm_provider() == "anthropic"


def test_ollama_needs_no_key():
    assert _settings(llm_provider="ollama").resolve_llm_provider() == "ollama"


# --- resolve_embedding_provider ------------------------------------------
def test_embedding_auto_follows_ollama_chat():
    assert _settings(llm_provider="ollama").resolve_embedding_provider() == "ollama"


def test_embedding_explicit_hashing():
    s = _settings(llm_provider="ollama", embedding_provider="hashing")
    assert s.resolve_embedding_provider() == "hashing"


def test_embedding_openai_requires_key():
    assert _settings(embedding_provider="openai").resolve_embedding_provider() == "hashing"
    s = _settings(embedding_provider="openai", openai_api_key="o")
    assert s.resolve_embedding_provider() == "openai"


# --- _build_client type mapping ------------------------------------------
def test_build_client_types():
    assert isinstance(_build_client("mock", _settings()), MockLLMClient)
    assert isinstance(
        _build_client("anthropic", _settings(anthropic_api_key="x")), AnthropicClient
    )
    for p in ("openai", "groq", "openrouter", "ollama"):
        assert isinstance(_build_client(p, _settings()), OpenAICompatibleClient)
