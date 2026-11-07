"""
Provider registry: the single place that knows which providers exist.

To add a new provider, implement ``LLMProvider`` (usually by subclassing
``HttpLLMProvider``) and call ``registry.register(provider)`` - nothing in the RAG
pipeline, service or API layer needs to change.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.errors import ProviderNotFoundError
from app.llm.groq import GroqProvider
from app.llm.ollama import OllamaProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.retry import RetryPolicy, SleepFn

# A cold local model can take a while to load into memory on the first request.
OLLAMA_MIN_TIMEOUT_SECONDS = 120.0


class ProviderRegistry:
    def __init__(self, providers: list[LLMProvider] | None = None) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._default_base_urls: dict[str, str] = {}
        for provider in providers or []:
            self.register(provider)

    # ---- construction ------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> "ProviderRegistry":
        common: dict[str, Any] = {
            "timeout": settings.llm_timeout_seconds,
            "retry_policy": RetryPolicy(max_retries=settings.llm_max_retries),
            "client": client,
            "sleep": sleep,
        }
        providers: list[LLMProvider] = [
            GroqProvider(
                api_key=settings.groq_api_key,
                base_url=settings.groq_base_url,
                default_model=settings.groq_default_model,
                **common,
            ),
            OpenAIProvider(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                default_model=settings.openai_default_model,
                **common,
            ),
            AnthropicProvider(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
                api_version=settings.anthropic_version,
                default_model=settings.anthropic_default_model,
                **common,
            ),
            OllamaProvider(
                base_url=settings.ollama_base_url,
                default_model=settings.ollama_default_model,
                **{**common, "timeout": max(settings.llm_timeout_seconds, OLLAMA_MIN_TIMEOUT_SECONDS)},
            ),
        ]
        for custom in settings.openai_compatible_providers:
            needs_key = bool(custom.api_key_env)
            providers.append(
                OpenAICompatibleProvider(
                    name=custom.name,
                    base_url=custom.base_url,
                    api_key=settings.resolve_custom_api_key(custom),
                    default_model=custom.default_model,
                    requires_api_key=needs_key,
                    # A provider that holds a credential must not be pointed at another host at runtime,
                    # otherwise the key could be sent to an attacker-controlled URL.
                    allows_base_url_override=not needs_key,
                    strict_model_check=False,
                    **common,
                )
            )
        return cls(providers)

    # ---- access --------------------------------------------------------------

    def register(self, provider: LLMProvider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Provider '{provider.name}' is already registered.")
        self._providers[provider.name] = provider
        self._default_base_urls[provider.name] = provider.base_url

    def get(self, name: str) -> LLMProvider:
        provider = self._providers.get((name or "").strip().lower())
        if provider is None:
            known = ", ".join(sorted(self._providers))
            raise ProviderNotFoundError(f"Unknown provider '{name}'. Available providers: {known}.")
        return provider

    def names(self) -> list[str]:
        return list(self._providers)

    def list_info(self) -> list[dict[str, Any]]:
        return [provider.public_info() for provider in self._providers.values()]

    # ---- runtime endpoint override ---------------------------------------------

    def set_base_url(self, name: str, url: str | None) -> str:
        """
        Point a provider at a different endpoint (``None`` restores the configured default).
        Returns the previous URL. Only providers without credentials may be redirected.
        """
        provider = self.get(name)
        if url is None:
            new_url = self._default_base_urls[provider.name]
        else:
            if not provider.allows_base_url_override:
                raise ValidationFailedError(
                    f"The endpoint of '{provider.name}' can only be changed in the server configuration.",
                    code="base_url_override_not_allowed",
                )
            new_url = url.strip().rstrip("/")
            if not new_url.startswith(("http://", "https://")):
                raise ValidationFailedError(
                    "Endpoint must start with http:// or https://.", code="invalid_base_url"
                )
        previous = provider.base_url
        provider.base_url = new_url
        return previous

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
