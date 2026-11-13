"""Centralized LLM Manager.

Agents never construct LLM clients; they call ``LLMManager.complete`` /
``complete_json``. The manager owns provider configuration, runtime provider and
model selection, timeouts, retries with backoff, rate-limit handling, provider
fallback, usage/latency/cost tracking and JSON schema validation.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.security.sanitize import mask_secrets

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("mock", "groq", "ollama", "openai_compatible")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
M = TypeVar("M", bound=BaseModel)


# ---------------------------------------------------------------- errors
class LLMError(Exception):
    """Base error. Messages are always secret-masked."""

    retriable = False


class LLMConfigError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    retriable = True


class LLMRateLimitError(LLMUnavailableError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMInvalidResponseError(LLMError):
    pass


# ---------------------------------------------------------------- data
@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost: float = 0.0
    attempts: int = 1
    fallback_used: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ProviderConfig:
    name: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    @property
    def configured(self) -> bool:
        if self.name == "mock":
            return True
        if self.name == "groq":
            return bool(self.api_key and self.model)
        return bool(self.base_url and self.model)


def resolve_provider_config(name: str, model: str | None = None) -> ProviderConfig:
    name = (name or "mock").lower()
    if name == "mock":
        return ProviderConfig("mock", model="mock")
    if name == "groq":
        cfg = ProviderConfig("groq", GROQ_BASE_URL, settings.groq_api_key.get_secret_value(), settings.groq_model)
    elif name == "ollama":
        cfg = ProviderConfig("ollama", settings.ollama_base_url, "ollama", settings.ollama_model)
    elif name == "openai_compatible":
        key = settings.llm_api_key.get_secret_value() or settings.openai_api_key.get_secret_value()
        cfg = ProviderConfig("openai_compatible", settings.llm_base_url, key, settings.llm_model)
    else:
        raise LLMConfigError(f"Unsupported LLM provider: {name}")
    if model:
        cfg.model = model
    return cfg


def provider_status() -> list[dict]:
    """Dashboard-safe provider overview (no secrets)."""
    out = []
    for name in SUPPORTED_PROVIDERS:
        cfg = resolve_provider_config(name)
        out.append({
            "provider": name,
            "configured": cfg.configured,
            "model": cfg.model,
            "base_url": cfg.base_url if name != "mock" else "",
            "has_api_key": bool(cfg.api_key) and name not in ("mock", "ollama"),
        })
    return out


# ---------------------------------------------------------------- providers
class BaseProvider:
    name = "base"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def chat(self, system: str, user: str, *, timeout: float, json_mode: bool, temperature: float) -> LLMResponse:
        raise NotImplementedError


class MockProvider(BaseProvider):
    """Deterministic offline provider.

    Returns ``""`` by default which tells agents to use their rule-based path.
    Tests can script responses via ``MockProvider.script`` (list of str/Exception).
    """

    name = "mock"
    script: list[Any] = []

    def chat(self, system, user, *, timeout, json_mode, temperature) -> LLMResponse:
        if MockProvider.script:
            item = MockProvider.script.pop(0)
            if isinstance(item, Exception):
                raise item
            text = item
        else:
            text = ""
        return LLMResponse(text=text, provider="mock", model="mock",
                           prompt_tokens=len(system.split()) + len(user.split()),
                           completion_tokens=len(text.split()))


class OpenAICompatibleProvider(BaseProvider):
    """Groq, Ollama and any OpenAI-compatible endpoint share this adapter."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.name = config.name
        if not config.configured:
            raise LLMConfigError(f"Provider '{config.name}' is not configured (base URL / API key / model missing)")
        self._client = None

    def _get_client(self, timeout: float):
        from openai import OpenAI

        # SDK-level retries disabled: the manager owns retry policy.
        return OpenAI(base_url=self.config.base_url, api_key=self.config.api_key or "local",
                      timeout=timeout, max_retries=0)

    def chat(self, system, user, *, timeout, json_mode, temperature) -> LLMResponse:
        import openai

        client = self._client or self._get_client(timeout)
        kwargs: dict[str, Any] = dict(
            model=self.config.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            try:
                resp = client.chat.completions.create(**kwargs)
            except openai.BadRequestError:
                if not json_mode:
                    raise
                kwargs.pop("response_format", None)  # endpoint without JSON mode support
                resp = client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            retry_after = None
            try:
                retry_after = float(exc.response.headers.get("retry-after", ""))
            except Exception:  # noqa: S110 - Retry-After is optional; backoff applies without it
                pass
            raise LLMRateLimitError(mask_secrets(f"{self.name} rate limited: {exc}"), retry_after) from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise LLMUnavailableError(mask_secrets(f"{self.name} unreachable: {type(exc).__name__}")) from exc
        except openai.InternalServerError as exc:
            raise LLMUnavailableError(mask_secrets(f"{self.name} server error: {exc}")) from exc
        except openai.AuthenticationError as exc:
            raise LLMConfigError(f"{self.name} authentication failed; check the API key") from exc
        except openai.APIError as exc:
            raise LLMError(mask_secrets(f"{self.name} API error: {exc}")) from exc
        latency = (time.perf_counter() - started) * 1000
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            provider=self.name,
            model=getattr(resp, "model", None) or self.config.model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=latency,
        )


def build_provider(config: ProviderConfig) -> BaseProvider:
    if config.name == "mock":
        return MockProvider(config)
    return OpenAICompatibleProvider(config)


# ---------------------------------------------------------------- JSON helpers
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> Any:
    """Parse JSON from model output, tolerating code fences and surrounding prose."""
    if not text or not text.strip():
        raise LLMInvalidResponseError("Empty response from model")
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMInvalidResponseError("Model response was not valid JSON")


def example_json(schema: type[BaseModel]) -> str:
    """An example object with placeholder values for a model.

    Models imitate whatever they are shown; a raw JSON Schema makes them reply with schema fragments
    ({"title": ..., "type": "string"}) instead of values, so we show a filled-in shape instead.
    """
    root = schema.model_json_schema()
    defs = root.get("$defs", {})

    def build(node: dict):
        if "$ref" in node:
            return build(defs[node["$ref"].split("/")[-1]])
        for key in ("anyOf", "oneOf"):
            if key in node:
                options = [o for o in node[key] if o.get("type") != "null"]
                return build(options[0]) if options else None
        kind = node.get("type")
        if kind == "object" or "properties" in node:
            return {name: build(sub) for name, sub in node.get("properties", {}).items()}
        if kind == "array":
            return [build(node.get("items", {}))]
        if kind == "integer":
            return 0
        if kind == "number":
            return 0.0
        if kind == "boolean":
            return False
        return "<text>"

    return json.dumps(build(root), ensure_ascii=False)


# ---------------------------------------------------------------- manager
UsageSink = Callable[[dict], None]


def _db_usage_sink(record: dict) -> None:
    """Default sink: persist usage; never let accounting break a workflow."""
    try:
        from app.db.base import session_scope
        from app.db.models import LLMUsage

        with session_scope() as session:
            session.add(LLMUsage(**record))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not persist LLM usage: %s", mask_secrets(str(exc)))


class LLMManager:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        fallbacks: list[str] | None = None,
        usage_sink: UsageSink | None = _db_usage_sink,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = (provider or settings.llm_provider).lower()
        self.model = model
        if self.provider not in SUPPORTED_PROVIDERS:
            raise LLMConfigError(f"Unsupported LLM provider: {self.provider}")
        if fallbacks is None:
            fallbacks = [p.strip().lower() for p in settings.llm_fallback_providers.split(",") if p.strip()]
        self.fallbacks = [p for p in fallbacks if p != self.provider]
        self.usage_sink = usage_sink
        self._sleep = sleep
        self.usage: list[LLMResponse] = []

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def ensure_usable(self) -> None:
        """Fail fast when neither the chosen provider nor any fallback is configured.

        Without this a mis-selected provider would silently degrade every agent to rules.
        """
        for name in [self.provider, *self.fallbacks]:
            if resolve_provider_config(name, self.model if name == self.provider else None).configured:
                return
        raise LLMConfigError(f"LLM provider '{self.provider}' is not configured "
                             "(missing API key, base URL or model) and no configured fallback exists")

    # -- public API ------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        agent: str = "",
        execution_id: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.1,
    ) -> str:
        return self.generate(system, user, provider=provider, model=model, agent=agent,
                             execution_id=execution_id, json_mode=json_mode, temperature=temperature).text

    def generate(
        self,
        system: str,
        user: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        agent: str = "",
        execution_id: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.1,
    ) -> LLMResponse:
        primary = (provider or self.provider).lower()
        chain = [primary] + [f for f in self.fallbacks if f != primary]
        last_error: LLMError | None = None
        for index, name in enumerate(chain):
            try:
                # An explicit/instance model override only applies to the primary provider.
                cfg = resolve_provider_config(name, (model or self.model) if index == 0 else None)
                resp = self._call_with_retries(build_provider(cfg), system, user, json_mode, temperature,
                                               agent, execution_id)
                resp.fallback_used = index > 0
                return resp
            except LLMError as exc:
                last_error = exc
                logger.warning("LLM provider %s failed (%s)", name, type(exc).__name__)
                self._record(name, "", agent, execution_id, None, exc)
        assert last_error is not None
        raise last_error

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[M],
        *,
        provider: str | None = None,
        model: str | None = None,
        agent: str = "",
        execution_id: str | None = None,
        repair_attempts: int = 1,
    ) -> M:
        """Return a validated model instance, asking the model to repair invalid output."""
        sys_prompt = (f"{system}\nRespond with ONLY a single JSON object of exactly this shape, replacing every "
                      f"placeholder with a real value (no schema, no commentary):\n{example_json(schema)}")
        prompt = user
        last: Exception | None = None
        for _ in range(repair_attempts + 1):
            resp = self.generate(sys_prompt, prompt, provider=provider, model=model, agent=agent,
                                 execution_id=execution_id, json_mode=True)
            try:
                return schema.model_validate(extract_json(resp.text))
            except (LLMInvalidResponseError, ValidationError) as exc:
                last = exc
                prompt = (f"{user}\n\nYour previous reply was invalid ({type(exc).__name__}). "
                          "Reply again with ONLY a valid JSON object matching the schema.")
        raise LLMInvalidResponseError(f"Model output failed schema validation: {type(last).__name__}")

    # -- internals -------------------------------------------------------
    def _call_with_retries(self, provider, system, user, json_mode, temperature, agent, execution_id) -> LLMResponse:
        max_attempts = max(1, settings.llm_max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                resp = provider.chat(system, user, timeout=settings.llm_timeout,
                                     json_mode=json_mode, temperature=temperature)
            except LLMError as exc:
                if exc.retriable and attempt < max_attempts:
                    delay = getattr(exc, "retry_after", None) or min(0.5 * (2 ** (attempt - 1)), 8.0)
                    self._sleep(min(float(delay), 30.0))
                    continue
                raise
            resp.attempts = attempt
            resp.estimated_cost = (resp.prompt_tokens / 1000 * settings.llm_cost_per_1k_input
                                   + resp.completion_tokens / 1000 * settings.llm_cost_per_1k_output)
            self.usage.append(resp)
            self._record(resp.provider, resp.model, agent, execution_id, resp, None)
            return resp
        raise LLMUnavailableError("LLM retries exhausted")  # pragma: no cover

    def _record(self, provider, model, agent, execution_id, resp: LLMResponse | None, error: Exception | None):
        if not self.usage_sink:
            return
        self.usage_sink({
            "workflow_execution_id": execution_id,
            "agent_name": agent,
            "provider": provider,
            "model": model,
            "prompt_tokens": resp.prompt_tokens if resp else 0,
            "completion_tokens": resp.completion_tokens if resp else 0,
            "latency_ms": resp.latency_ms if resp else 0.0,
            "estimated_cost": resp.estimated_cost if resp else 0.0,
            "success": error is None,
            "error": mask_secrets(str(error))[:500] if error else None,
        })

    def usage_summary(self) -> dict:
        return {
            "calls": len(self.usage),
            "prompt_tokens": sum(u.prompt_tokens for u in self.usage),
            "completion_tokens": sum(u.completion_tokens for u in self.usage),
            "estimated_cost": sum(u.estimated_cost for u in self.usage),
            "calls_detail": [asdict(u) for u in self.usage],
        }
