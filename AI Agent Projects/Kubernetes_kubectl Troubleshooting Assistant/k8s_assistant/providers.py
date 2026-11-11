"""LLM provider presets.

All three supported providers (OpenAI, Groq, Ollama) expose an
OpenAI-compatible Chat Completions API, so the same analyzer client works for
each; only the base URL, default model, and API-key handling differ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    """Static description of an LLM provider."""

    key: str  # stable identifier, e.g. "openai"
    label: str  # human-friendly name shown in the UI
    base_url: str | None  # OpenAI-compatible endpoint (None = library default)
    default_model: str
    suggested_models: list[str] = field(default_factory=list)
    requires_api_key: bool = True
    api_key_env: str = ""  # env var the key is read from
    model_env: str = ""  # env var overriding the default model
    # For local providers we still must pass a non-empty key to the OpenAI
    # client, even though it is ignored by the server.
    placeholder_key: str = "not-needed"

    @property
    def key_help(self) -> str:
        if not self.requires_api_key:
            return "No API key required for a local Ollama server."
        return f"Read from {self.api_key_env} if left blank."


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        key="openai",
        label="OpenAI",
        base_url=None,  # openai library defaults to https://api.openai.com/v1
        default_model="gpt-4o-mini",
        suggested_models=["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        requires_api_key=True,
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
    ),
    "groq": Provider(
        key="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        suggested_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
            "qwen/qwen3-32b",
        ],
        requires_api_key=True,
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
    ),
    "ollama": Provider(
        key="ollama",
        label="Ollama (local)",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        suggested_models=["llama3.1", "llama3.2", "qwen2.5", "mistral", "phi3"],
        requires_api_key=False,
        api_key_env="OLLAMA_API_KEY",
        model_env="OLLAMA_MODEL",
        placeholder_key="ollama",
    ),
}

DEFAULT_PROVIDER = "openai"


def get_provider(key: str) -> Provider:
    """Look up a provider by key, falling back to the default."""
    return PROVIDERS.get(key, PROVIDERS[DEFAULT_PROVIDER])


def provider_api_key(provider: Provider) -> str | None:
    """Resolve the API key for a provider from its environment variable."""
    if not provider.api_key_env:
        return None
    return os.getenv(provider.api_key_env) or None


def provider_model(provider: Provider) -> str:
    """Resolve the model for a provider: env override, else the default."""
    if provider.model_env:
        return os.getenv(provider.model_env) or provider.default_model
    return provider.default_model


def provider_base_url(provider: Provider) -> str | None:
    """Resolve the base URL, allowing a per-provider env override.

    For Ollama, OLLAMA_BASE_URL can point at a remote host. OpenAI can be
    redirected with OPENAI_BASE_URL (e.g. Azure / a proxy).
    """
    env_name = f"{provider.key.upper()}_BASE_URL"
    return os.getenv(env_name) or provider.base_url
