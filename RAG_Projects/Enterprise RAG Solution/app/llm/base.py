"""
Provider-independent LLM interface.

The RAG pipeline only talks to ``LLMProvider``. Adding a provider means writing one
subclass (usually of ``HttpLLMProvider``) and registering it - no changes to retrieval,
prompting or the API layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from app.llm.errors import (
    InvalidRequestError,
    LLMError,
    ModelNotSelectedError,
    ProviderNotConfiguredError,
)
from app.llm.http_errors import map_http_error, map_transport_error
from app.llm.retry import RetryPolicy, SleepFn, stream_with_retries, with_retries
from app.llm.sse import parse_json_object

logger = logging.getLogger("app.llm")

Role = Literal["system", "user", "assistant"]


# --------------------------------------------------------------------------- data types


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class GenerationParams:
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout: float | None = None   # None -> the provider's default timeout


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: Usage | None = None
    finish_reason: str | None = None
    latency_ms: float = 0.0
    attempts: int = 1
    # Filled in by the service when a fallback provider produced this answer.
    used_fallback: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None


@dataclass
class StreamEvent:
    """One piece of a streamed answer. The last event of a stream has ``done=True``."""

    delta: str = ""
    done: bool = False
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str | None = None
    size_bytes: int | None = None


@dataclass
class ConnectionStatus:
    ok: bool
    provider: str
    message: str
    latency_ms: float = 0.0
    model_count: int | None = None
    model_available: bool | None = None
    error_code: str | None = None


# --------------------------------------------------------------------------- interface


class LLMProvider(ABC):
    """Contract every generation provider fulfils."""

    name: str
    kind: str                             # "openai_compatible" | "ollama" | "anthropic"
    requires_api_key: bool = True
    allows_base_url_override: bool = False
    supports_streaming: bool = True
    strict_model_check: bool = True       # False: don't fail connection tests on unlisted models

    base_url: str
    default_model: str = ""

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True when the provider can be called (API key present, if one is required)."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Models the provider reports as available."""

    @abstractmethod
    async def generate(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        """Return a complete answer."""

    @abstractmethod
    def stream(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        """Yield the answer incrementally."""

    async def aclose(self) -> None:
        """Release network resources."""

    # ---- shared behaviour --------------------------------------------------

    def resolve_model(self, requested: str | None) -> str:
        model = (requested or self.default_model or "").strip()
        if not model:
            raise ModelNotSelectedError(
                f"No model selected for {self.name}. Choose one from its model list "
                f"or set a default model in the configuration.",
                provider=self.name,
            )
        return model

    def model_matches(self, wanted: str, available_ids: Collection[str]) -> bool:
        return wanted in available_ids

    async def test_connection(self, model: str | None = None) -> ConnectionStatus:
        """Cheap reachability/credential check (lists models; never generates text)."""
        if not self.is_configured:
            return ConnectionStatus(
                ok=False,
                provider=self.name,
                message=f"{self.name} is not configured: API key missing.",
                error_code=ProviderNotConfiguredError.code,
            )
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except LLMError as exc:
            return ConnectionStatus(
                ok=False,
                provider=self.name,
                message=exc.message,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=exc.code,
            )
        latency = (time.perf_counter() - started) * 1000

        wanted = (model or self.default_model or "").strip()
        available: bool | None = None
        if wanted and self.strict_model_check:
            available = self.model_matches(wanted, {m.id for m in models})

        if available is False:
            return ConnectionStatus(
                ok=False,
                provider=self.name,
                message=f"Connected, but model '{wanted}' is not available on {self.name}.",
                latency_ms=latency,
                model_count=len(models),
                model_available=False,
                error_code="model_not_found",
            )
        return ConnectionStatus(
            ok=True,
            provider=self.name,
            message=f"Connected to {self.name}.",
            latency_ms=latency,
            model_count=len(models),
            model_available=available,
        )

    def public_info(self) -> dict[str, Any]:
        """Non-secret description of the provider (safe to return from the API)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "configured": self.is_configured,
            "requires_api_key": self.requires_api_key,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "allows_base_url_override": self.allows_base_url_override,
            "supports_streaming": self.supports_streaming,
        }


# --------------------------------------------------------------------------- HTTP base


class HttpLLMProvider(LLMProvider):
    """
    Shared plumbing for providers that speak HTTP: one pooled client, timeouts,
    status/transport error mapping, retries and logging.

    Subclasses implement ``_generate_once`` / ``_stream_once`` / ``list_models`` and
    ``_auth_headers``.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: SecretStr | None = None,
        default_model: str = "",
        timeout: float = 60.0,
        retry_policy: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._no_temperature_models: set[str] = set()

    def __repr__(self) -> str:   # never include the key
        return f"<{type(self).__name__} name={self.name!r} base_url={self.base_url!r} configured={self.is_configured}>"

    # ---- configuration -----------------------------------------------------

    @property
    def api_key(self) -> str | None:
        if self._api_key is None:
            return None
        value = self._api_key.get_secret_value().strip()
        return value or None

    @property
    def is_configured(self) -> bool:
        return (not self.requires_api_key) or self.api_key is not None

    def _require_ready(self) -> None:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"{self.name} is not configured: API key missing.", provider=self.name
            )

    def _auth_headers(self) -> dict[str, str]:
        return {}

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json", **self._auth_headers()}

    async def _retrying(self, operation):
        """Run any single-shot call (for example listing models) under the retry policy."""
        result, _ = await with_retries(
            operation, self.retry_policy, sleep=self._sleep, on_retry=self._log_retry
        )
        return result

    # ---- models that reject a custom temperature ----------------------------
    # Some newer models (e.g. reasoning models) refuse a non-default temperature. When a provider
    # tells us so, we remember it for that model and resend the request without the parameter.

    def _temperature_for(self, model: str, value: float) -> float | None:
        return None if model in self._no_temperature_models else value

    def _should_drop_temperature(self, error: LLMError, model: str) -> bool:
        if not isinstance(error, InvalidRequestError) or model in self._no_temperature_models:
            return False
        if "temperature" not in error.message.lower():
            return False
        self._no_temperature_models.add(model)
        logger.info("llm_param_adjusted provider=%s model=%s dropped=temperature", self.name, model)
        return True

    # ---- transport ---------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)))
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _error_from_response(
        self, status: int, headers: Mapping[str, str], body: str, model: str | None
    ) -> LLMError:
        return map_http_error(provider=self.name, model=model, status=status, headers=headers, body=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """One HTTP attempt. Raises a mapped ``LLMError`` on any failure."""
        client = await self._get_client()
        try:
            response = await client.request(
                method,
                self.base_url + path,
                json=json_body,
                headers=self._headers(),
                timeout=timeout or self.timeout,
            )
        except httpx.HTTPError as exc:
            raise map_transport_error(provider=self.name, model=model, exc=exc, base_url=self.base_url) from exc
        if response.status_code >= 400:
            raise self._error_from_response(response.status_code, response.headers, response.text, model)
        return response

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        response = await self._request(method, path, json_body=json_body, model=model, timeout=timeout)
        return parse_json_object(response.text, provider=self.name)

    async def _stream_lines(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        model: str | None,
        timeout: float | None,
    ) -> AsyncIterator[str]:
        """Open a streaming POST and yield decoded lines; errors are mapped like ``_request``."""
        client = await self._get_client()
        try:
            async with client.stream(
                "POST",
                self.base_url + path,
                json=json_body,
                headers={**self._headers(), "Accept": "text/event-stream, application/x-ndjson"},
                timeout=timeout or self.timeout,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._error_from_response(response.status_code, response.headers, body, model)
                async for line in response.aiter_lines():
                    yield line
        except httpx.HTTPError as exc:
            raise map_transport_error(provider=self.name, model=model, exc=exc, base_url=self.base_url) from exc

    # ---- template methods --------------------------------------------------

    @abstractmethod
    async def _generate_once(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        """A single non-streaming attempt (no retries)."""

    @abstractmethod
    def _stream_once(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        """A single streaming attempt (no retries)."""

    def _log_retry(self, attempt: int, error: LLMError, delay: float) -> None:
        logger.warning(
            "llm_retry provider=%s attempt=%d code=%s wait=%.1fs", self.name, attempt, error.code, delay
        )

    async def generate(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        self._require_ready()
        started = time.perf_counter()
        result, attempts = await with_retries(
            lambda: self._generate_once(messages, params),
            self.retry_policy,
            sleep=self._sleep,
            on_retry=self._log_retry,
        )
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.attempts = attempts
        logger.info(
            "llm_call provider=%s model=%s latency_ms=%.0f prompt_tokens=%s completion_tokens=%s attempts=%d",
            result.provider,
            result.model,
            result.latency_ms,
            result.usage.prompt_tokens if result.usage else None,
            result.usage.completion_tokens if result.usage else None,
            attempts,
        )
        return result

    async def stream(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        self._require_ready()
        async for event in stream_with_retries(
            lambda: self._stream_once(messages, params),
            self.retry_policy,
            sleep=self._sleep,
            on_retry=self._log_retry,
        ):
            yield event
