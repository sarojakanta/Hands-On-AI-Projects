"""
LLM service: runtime provider selection, validation before activation, and optional fallback.

This is the only object the rest of the application (RAG pipeline, API routes) uses to
generate text. Switching provider/model here never touches embeddings or the vector store.

Fallback rules
--------------
* Only used when enabled (configuration or per request) and the failure is
  ``fallback_eligible`` (unavailable, timeout, rate limit, quota, bad credentials, ...).
  Invalid requests and unknown models are *not* retried elsewhere - they are the caller's bug.
* Never silent: the result carries ``used_fallback``, ``fallback_from`` and ``fallback_reason``,
  and streams announce the provider that is actually answering in their ``StreamStart`` item.
* A request that explicitly names a provider is never redirected unless it also sets
  ``allow_fallback=True``.
* For streams, fallback is only possible before the first token. After that, a failure is
  reported as ``StreamInterruptedError`` because partial text cannot be spliced.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.core.errors import AppError, ValidationFailedError
from app.llm.base import (
    ChatMessage,
    ConnectionStatus,
    GenerationParams,
    LLMProvider,
    LLMResult,
    StreamEvent,
)
from app.llm.errors import LLMError
from app.llm.persistence import ConfigPersistence
from app.llm.registry import ProviderRegistry

logger = logging.getLogger("app.llm")


# --------------------------------------------------------------------------- config models


class LLMRuntimeConfig(BaseModel):
    """Active generation settings. Contains no secrets, so it is safe to return from the API."""

    provider: str
    model: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    fallback_enabled: bool = False
    fallback_provider: str = ""
    fallback_model: str = ""
    base_url_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider", "fallback_provider")
    @classmethod
    def _normalise_name(cls, value: str) -> str:
        return value.strip().lower()


class LLMConfigUpdate(BaseModel):
    """Partial update. ``base_url_overrides`` values of ``null`` restore the default endpoint."""

    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    fallback_enabled: bool | None = None
    fallback_provider: str | None = Field(default=None, max_length=32)
    fallback_model: str | None = Field(default=None, max_length=200)
    base_url_overrides: dict[str, str | None] | None = None


class ProviderValidationError(AppError):
    status_code = 422
    code = "provider_validation_failed"


@dataclass
class StreamStart:
    """First item of a stream: says which provider/model is actually answering."""

    provider: str
    model: str
    used_fallback: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None


StreamItem = StreamStart | StreamEvent


# --------------------------------------------------------------------------- service


class LLMService:
    def __init__(
        self,
        registry: ProviderRegistry,
        config: LLMRuntimeConfig,
        persistence: ConfigPersistence | None = None,
        defaults: LLMRuntimeConfig | None = None,
    ) -> None:
        self.registry = registry
        self._config = config
        self._persistence = persistence
        self._defaults = (defaults or config).model_copy(deep=True)   # what "reset" returns to (the .env values)

    # ---- configuration ---------------------------------------------------------

    @property
    def config(self) -> LLMRuntimeConfig:
        return self._config

    async def update_config(self, update: LLMConfigUpdate, *, validate: bool = True) -> LLMRuntimeConfig:
        """
        Apply a configuration change. With ``validate=True`` (default) the new primary provider
        (and fallback, if enabled) must pass a live connection test before anything is activated.
        On failure the previous configuration and endpoints stay in force.
        """
        current = self._config
        # Fields sent as null mean "leave unchanged".
        changes = {
            key: value
            for key, value in update.model_dump(exclude_unset=True, exclude={"base_url_overrides"}).items()
            if value is not None
        }
        for key in ("provider", "fallback_provider"):
            if key in changes:
                changes[key] = changes[key].strip().lower()
        if not changes.get("provider", "x"):
            del changes["provider"]   # an empty provider name is ignored
        # A model id belongs to one provider: reset it when only the provider changes.
        if changes.get("provider", current.provider) != current.provider and "model" not in changes:
            changes["model"] = ""

        overrides = dict(current.base_url_overrides)
        previous_urls: dict[str, str] = {}
        try:
            for name, url in (update.base_url_overrides or {}).items():
                key = name.strip().lower()
                previous_urls.setdefault(key, self.registry.set_base_url(key, url))
                if url is None:
                    overrides.pop(key, None)
                else:
                    overrides[key] = url.strip().rstrip("/")

            candidate = current.model_copy(update={**changes, "base_url_overrides": overrides})
            self.registry.get(candidate.provider)   # unknown provider -> 404
            if candidate.fallback_enabled:
                self._check_fallback_settings(candidate)

            if validate:
                await self._validate_active(candidate.provider, candidate.model, role="primary")
                if candidate.fallback_enabled:
                    await self._validate_active(candidate.fallback_provider, candidate.fallback_model, role="fallback")
        except Exception:
            for name, url in previous_urls.items():   # roll endpoint changes back
                self.registry.get(name).base_url = url
            raise

        self._config = LLMRuntimeConfig.model_validate(candidate.model_dump())
        self._save()
        logger.info(
            "llm_config_updated provider=%s model=%s fallback=%s",
            self._config.provider,
            self._config.model or "(provider default)",
            self._config.fallback_provider if self._config.fallback_enabled else "off",
        )
        return self._config

    def _save(self) -> None:
        if self._persistence is None:
            return
        try:
            self._persistence.save(self._config.model_dump())
        except Exception:   # noqa: BLE001 - failing to persist must not break an otherwise valid change
            logger.exception("llm_config_persist_failed")

    def reset_config(self) -> LLMRuntimeConfig:
        """Return to the configuration given by the environment (.env) and forget the saved one."""
        for name in list(self._config.base_url_overrides):
            try:
                self.registry.set_base_url(name, None)
            except Exception:   # noqa: BLE001
                logger.warning("llm_reset_override_failed provider=%s", name)
        self._config = self._defaults.model_copy(deep=True)
        if self._persistence is not None:
            try:
                self._persistence.clear()
            except Exception:   # noqa: BLE001
                logger.exception("llm_config_clear_failed")
        logger.info("llm_config_reset provider=%s", self._config.provider)
        return self._config

    def _check_fallback_settings(self, candidate: LLMRuntimeConfig) -> None:
        if not candidate.fallback_provider:
            raise ValidationFailedError(
                "Choose a fallback provider to enable fallback.", code="fallback_provider_required"
            )
        if candidate.fallback_provider == candidate.provider:
            raise ValidationFailedError(
                "The fallback provider must differ from the primary provider.", code="fallback_same_as_primary"
            )
        self.registry.get(candidate.fallback_provider)

    async def _validate_active(self, provider_name: str, model: str, *, role: str) -> None:
        provider = self.registry.get(provider_name)
        effective_model = (model or provider.default_model).strip()
        if not effective_model:
            raise ProviderValidationError(
                f"Select a model for the {role} provider '{provider_name}'.",
                code="model_not_selected",
                details={"provider": provider_name, "role": role},
            )
        status = await provider.test_connection(effective_model)
        if not status.ok:
            raise ProviderValidationError(
                f"Cannot activate {role} provider '{provider_name}': {status.message}",
                details={
                    "provider": provider_name,
                    "role": role,
                    "reason": status.error_code or "connection_failed",
                },
            )

    async def test_provider(self, name: str, model: str | None = None) -> ConnectionStatus:
        return await self.registry.get(name).test_connection(model)

    # ---- resolution ------------------------------------------------------------

    def _params(
        self,
        provider: LLMProvider,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> GenerationParams:
        cfg = self._config
        return GenerationParams(
            model=provider.resolve_model(model),
            temperature=cfg.temperature if temperature is None else temperature,
            max_tokens=cfg.max_tokens if max_tokens is None else max_tokens,
        )

    def _resolve_primary(
        self,
        provider_name: str | None,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> tuple[LLMProvider, GenerationParams, bool]:
        cfg = self._config
        explicit = bool(provider_name) and provider_name.strip().lower() != cfg.provider
        provider = self.registry.get(provider_name or cfg.provider)
        # The configured model only applies to the configured provider.
        model_choice = model or (None if explicit else cfg.model) or None
        return provider, self._params(provider, model_choice, temperature, max_tokens), explicit

    def _fallback_plan(
        self,
        primary: LLMProvider,
        error: LLMError,
        *,
        explicit: bool,
        allow_fallback: bool | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> tuple[LLMProvider, GenerationParams] | None:
        cfg = self._config
        allowed = allow_fallback if allow_fallback is not None else (cfg.fallback_enabled and not explicit)
        if not allowed or not error.fallback_eligible:
            return None
        if not cfg.fallback_provider or cfg.fallback_provider == primary.name:
            return None
        try:
            provider = self.registry.get(cfg.fallback_provider)
            return provider, self._params(provider, cfg.fallback_model or None, temperature, max_tokens)
        except LLMError as exc:
            logger.warning("llm_fallback_unavailable reason=%s", exc.code)
            return None

    @staticmethod
    def _note_primary_failure(error: LLMError, primary: LLMProvider, cause: LLMError) -> LLMError:
        error.details["primary_failure"] = {"provider": primary.name, "code": cause.code}
        return error

    # ---- generation ------------------------------------------------------------

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        allow_fallback: bool | None = None,
    ) -> LLMResult:
        primary, params, explicit = self._resolve_primary(provider, model, temperature, max_tokens)
        try:
            return await primary.generate(messages, params)
        except LLMError as exc:
            plan = self._fallback_plan(
                primary, exc, explicit=explicit, allow_fallback=allow_fallback,
                temperature=temperature, max_tokens=max_tokens,
            )
            if plan is None:
                raise
            fallback, fb_params = plan
            logger.warning(
                "llm_fallback primary=%s fallback=%s reason=%s", primary.name, fallback.name, exc.code
            )
            try:
                result = await fallback.generate(messages, fb_params)
            except LLMError as fb_exc:
                raise self._note_primary_failure(fb_exc, primary, exc) from exc
            result.used_fallback = True
            result.fallback_from = primary.name
            result.fallback_reason = exc.code
            return result

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        allow_fallback: bool | None = None,
    ) -> AsyncIterator[StreamItem]:
        """
        Yield a ``StreamStart`` (emitted once the first token has arrived, so errors before that
        point raise normally and can still become proper HTTP errors) followed by ``StreamEvent`` items.
        """
        primary, params, explicit = self._resolve_primary(provider, model, temperature, max_tokens)
        start: StreamStart
        source = primary.stream(messages, params)
        try:
            first = await self._first_event(source)
            start = StreamStart(provider=primary.name, model=params.model)
        except LLMError as exc:
            plan = self._fallback_plan(
                primary, exc, explicit=explicit, allow_fallback=allow_fallback,
                temperature=temperature, max_tokens=max_tokens,
            )
            if plan is None:
                raise
            fallback, fb_params = plan
            logger.warning(
                "llm_fallback primary=%s fallback=%s reason=%s", primary.name, fallback.name, exc.code
            )
            source = fallback.stream(messages, fb_params)
            try:
                first = await self._first_event(source)
            except LLMError as fb_exc:
                raise self._note_primary_failure(fb_exc, primary, exc) from exc
            start = StreamStart(
                provider=fallback.name,
                model=fb_params.model,
                used_fallback=True,
                fallback_from=primary.name,
                fallback_reason=exc.code,
            )

        yield start
        if first is not None:
            yield first
        async for event in source:
            yield event

    @staticmethod
    async def _first_event(source: AsyncIterator[StreamEvent]) -> StreamEvent | None:
        try:
            return await source.__anext__()
        except StopAsyncIteration:
            return None


# --------------------------------------------------------------------------- factory


def build_llm_service(
    settings: Settings,
    registry: ProviderRegistry | None = None,
    persistence: ConfigPersistence | None = None,
) -> LLMService:
    """
    Build the service from the environment. If a configuration was saved earlier (changed through the
    API/UI) it wins over the .env defaults, so provider/model choices survive restarts.
    """
    registry = registry or ProviderRegistry.from_settings(settings)
    defaults = LLMRuntimeConfig(
        provider=settings.llm_default_provider,
        model=settings.llm_default_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        fallback_enabled=settings.llm_fallback_enabled,
        fallback_provider=settings.llm_fallback_provider,
        fallback_model=settings.llm_fallback_model,
    )
    config = defaults
    saved = _load_saved_config(persistence, registry)
    if saved is not None:
        config = saved
    if config.provider not in registry.names():
        logger.warning("llm_default_provider_unknown name=%s available=%s", config.provider, registry.names())
    return LLMService(registry, config, persistence, defaults)


def _load_saved_config(persistence: ConfigPersistence | None, registry: ProviderRegistry) -> LLMRuntimeConfig | None:
    """Read the saved configuration; anything unusable is ignored (the .env defaults apply)."""
    if persistence is None:
        return None
    try:
        raw = persistence.load()
        if not raw:
            return None
        saved = LLMRuntimeConfig.model_validate(raw)
    except Exception:   # noqa: BLE001 - a corrupt saved value must never stop the application starting
        logger.warning("llm_saved_config_ignored (invalid)")
        return None
    applied: dict[str, str] = {}
    for name, url in saved.base_url_overrides.items():
        try:
            registry.set_base_url(name, url)
            applied[name] = url
        except Exception:   # noqa: BLE001 - e.g. a provider that no longer allows overrides
            logger.warning("llm_saved_override_ignored provider=%s", name)
    saved.base_url_overrides = applied
    logger.info("llm_saved_config_loaded provider=%s model=%s", saved.provider, saved.model or "(default)")
    return saved


def result_to_dict(result: LLMResult) -> dict[str, Any]:
    usage = result.usage
    return {
        "text": result.text,
        "provider": result.provider,
        "model": result.model,
        "finish_reason": result.finish_reason,
        "latency_ms": round(result.latency_ms, 1),
        "attempts": result.attempts,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
        "used_fallback": result.used_fallback,
        "fallback_from": result.fallback_from,
        "fallback_reason": result.fallback_reason,
    }
