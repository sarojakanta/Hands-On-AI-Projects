"""Tests for multi-provider LLM resolution.

No live API calls - these only test which provider/base_url/model the service
selects given which keys and LLM_PROVIDER value are present. Provider config is
patched directly on the config object.
"""

from __future__ import annotations

import pytest

from app.config import config
from app.services import llm_service as m


@pytest.fixture
def clear_providers(monkeypatch):
    """Start every test with no keys and auto selection."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    for attr in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GROQ_API_KEY",
        "GROQ_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.setattr(config, attr, "")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(config, "GROQ_MODEL", "llama-3.1-8b-instant")
    monkeypatch.setattr(config, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "openai/gpt-4o-mini")
    yield


def test_no_keys_means_not_configured(clear_providers):
    assert m.resolve_provider() is None
    svc = m.LLMService()
    assert svc.configured is False
    assert svc.provider == "none"


def test_auto_prefers_groq_over_openai_and_openrouter(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_x")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk_x")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "or_x")
    resolved = m.resolve_provider()
    assert resolved.name == "groq"
    assert resolved.base_url == "https://api.groq.com/openai/v1"
    assert resolved.model == "llama-3.1-8b-instant"


def test_auto_falls_back_to_openai_when_no_groq(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk_x")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "or_x")
    resolved = m.resolve_provider()
    assert resolved.name == "openai"
    # OpenAI uses the SDK native default -> base_url None.
    assert resolved.base_url is None


def test_auto_uses_anthropic_third(clear_providers, monkeypatch):
    # Priority: groq -> openai -> anthropic -> openrouter
    # With only anthropic and openrouter, anthropic wins
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "or_x")
    resolved = m.resolve_provider()
    assert resolved.name == "anthropic"
    assert resolved.base_url == "https://api.anthropic.com/v1"


def test_auto_uses_openrouter_as_last_resort(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "or_x")
    resolved = m.resolve_provider()
    assert resolved.name == "openrouter"
    assert resolved.base_url == "https://openrouter.ai/api/v1"


def test_explicit_provider_overrides_auto(clear_providers, monkeypatch):
    # Both keys present, but force OpenAI explicitly.
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_x")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk_x")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    resolved = m.resolve_provider()
    assert resolved.name == "openai"


def test_explicit_provider_without_key_is_none(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    # No groq key set.
    assert m.resolve_provider() is None


def test_base_url_override_is_honoured(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_x")
    monkeypatch.setattr(config, "GROQ_BASE_URL", "https://proxy.internal/v1")
    resolved = m.resolve_provider("groq")
    assert resolved.base_url == "https://proxy.internal/v1"


# ---------------------------------------------------------------- ollama

def test_ollama_resolves_when_explicit(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_API_KEY", "ollama")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "llama3.1")
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "")
    resolved = m.resolve_provider("ollama")
    assert resolved is not None
    assert resolved.name == "ollama"
    assert resolved.base_url == "http://localhost:11434/v1"
    assert resolved.model == "llama3.1"


def test_ollama_configured_even_without_real_key(clear_providers, monkeypatch):
    # Dummy key is enough; no real credential required.
    monkeypatch.setattr(config, "OLLAMA_API_KEY", "ollama")
    resolved = m.resolve_provider("ollama")
    assert resolved and resolved.api_key


def test_ollama_excluded_from_auto(clear_providers, monkeypatch):
    # Even with Ollama "available", auto must never select it. With no other
    # keys set, auto resolves to None rather than falling back to Ollama.
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "OLLAMA_API_KEY", "ollama")
    assert m.resolve_provider() is None


def test_ollama_base_url_override(clear_providers, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:9999/v1")
    resolved = m.resolve_provider("ollama")
    assert resolved.base_url == "http://127.0.0.1:9999/v1"
