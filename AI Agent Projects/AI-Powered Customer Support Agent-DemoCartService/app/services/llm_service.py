"""Multi-provider LLM service.

A thin wrapper around any OpenAI-API-compatible chat-completions endpoint.
Because OpenAI, Groq, and OpenRouter all speak the same protocol, a single
``openai`` Python client talks to all three - only the base URL, key, and model
differ. This is the ONLY place the app talks to the LLM.

Provider selection (``LLM_PROVIDER``):
  * "auto" (default) - pick the first provider that has a key, in priority
    order: groq -> openai -> anthropic -> openrouter.
  * "openai" / "groq" / "anthropic" / "openrouter" - force a specific provider.

Base URLs are resolved in code (per-provider defaults), so ``*_BASE_URL`` env
vars can stay blank. A non-blank override is honoured for proxies/Azure/etc.

Design points:
  * Keys come from the environment and are NEVER logged.
  * ``configured`` reports whether a usable provider+key exists so the agent can
    degrade gracefully (and tests can run fully offline).
  * ``chat`` returns plain text; ``structured`` returns parsed JSON via a
    tolerant extractor, since not every model honours strict JSON mode.
  * ``latency_ms`` is captured per call for tracing (Phase 8).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from app.config import config


# Per-provider default base URLs. OpenAI is None => let the SDK use its native
# default (api.openai.com). Ollama points at the local server.
# Anthropic uses the native Anthropic SDK, not OpenAI-compatible endpoint.
_PROVIDER_DEFAULTS = {
    "openai": {"base_url": None},
    "groq": {"base_url": "https://api.groq.com/openai/v1"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    "ollama": {"base_url": "http://localhost:11434/v1"},
}

# Order used when LLM_PROVIDER=auto: prefer direct (cheaper/faster) over the
# gateway. Ollama is intentionally EXCLUDED from auto - it is only used when
# LLM_PROVIDER=ollama is set explicitly, since it depends on a locally running
# server that may not be present.
_AUTO_PRIORITY = ("groq", "openai", "anthropic", "openrouter")


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: int
    provider: str = ""
    raw: dict | None = field(default=None, repr=False)


class LLMNotConfiguredError(RuntimeError):
    """Raised when an LLM call is attempted without a configured provider."""


@dataclass
class _ResolvedProvider:
    name: str
    api_key: str
    model: str
    base_url: str | None


def _provider_settings(name: str) -> _ResolvedProvider | None:
    """Build a resolved provider from config, or None if it has no key."""
    if name == "openai":
        key, model, override = config.OPENAI_API_KEY, config.OPENAI_MODEL, config.OPENAI_BASE_URL
    elif name == "groq":
        key, model, override = config.GROQ_API_KEY, config.GROQ_MODEL, config.GROQ_BASE_URL
    elif name == "anthropic":
        key, model, override = (
            config.ANTHROPIC_API_KEY,
            config.ANTHROPIC_MODEL,
            config.ANTHROPIC_BASE_URL,
        )
    elif name == "openrouter":
        key, model, override = (
            config.OPENROUTER_API_KEY,
            config.OPENROUTER_MODEL,
            config.OPENROUTER_BASE_URL,
        )
    elif name == "ollama":
        # Ollama needs no real key; the OpenAI SDK just requires a non-empty
        # string, so a dummy is fine. It is "configured" as long as selected.
        key = config.OLLAMA_API_KEY or "ollama"
        model, override = config.OLLAMA_MODEL, config.OLLAMA_BASE_URL
    else:
        return None

    if not key:
        return None

    base_url = _clean_override(override) or _PROVIDER_DEFAULTS[name]["base_url"]
    return _ResolvedProvider(name=name, api_key=key, model=model, base_url=base_url)


def _clean_override(value: str | None) -> str | None:
    """Return a usable base-URL override, or None.

    Guards against a common .env mistake where an inline comment leaks into the
    value (e.g. "# blank -> default ..."). Any value that is empty or does not
    look like an http(s) URL is treated as "no override".
    """
    if not value:
        return None
    v = value.strip()
    if not v or v.startswith("#"):
        return None
    if not (v.startswith("http://") or v.startswith("https://")):
        return None
    return v


def resolve_provider(preference: str | None = None) -> _ResolvedProvider | None:
    """Resolve which provider to use given LLM_PROVIDER (or an override).

    Returns None when no provider has a key (offline/deterministic mode).
    """
    pref = (preference or config.LLM_PROVIDER or "auto").strip().lower()

    if pref in _PROVIDER_DEFAULTS:
        return _provider_settings(pref)

    # auto (or unknown value) -> first configured provider by priority.
    for name in _AUTO_PRIORITY:
        resolved = _provider_settings(name)
        if resolved:
            return resolved
    return None


class LLMService:
    def __init__(self, preference: str | None = None) -> None:
        self._preference = preference
        self._client = None  # lazily created
        self._resolved = resolve_provider(preference)

    def refresh(self) -> None:
        """Re-resolve the provider (e.g. after env changes in tests)."""
        self._resolved = resolve_provider(self._preference)
        self._client = None

    @property
    def provider(self) -> str:
        return self._resolved.name if self._resolved else "none"

    @property
    def model(self) -> str:
        return self._resolved.model if self._resolved else ""

    @property
    def configured(self) -> bool:
        return self._resolved is not None

    def _get_client(self):
        if self._resolved is None:
            raise LLMNotConfiguredError("No LLM provider is configured (set a provider key).")
        if self._client is None:
            # Anthropic uses its native SDK with a different API structure
            if self._resolved.name == "anthropic":
                try:
                    from anthropic import Anthropic
                    self._client = Anthropic(api_key=self._resolved.api_key)
                except ImportError as exc:
                    raise RuntimeError(
                        "The 'anthropic' package is required when using Anthropic provider. "
                        "Install it with: pip install anthropic"
                    ) from exc
            else:
                # All other providers use OpenAI-compatible API
                from openai import OpenAI

                kwargs: dict = {"api_key": self._resolved.api_key}
                if self._resolved.base_url:
                    kwargs["base_url"] = self._resolved.base_url
                # OpenRouter recommends attribution headers; harmless elsewhere.
                if self._resolved.name == "openrouter":
                    kwargs["default_headers"] = {
                        "HTTP-Referer": config.OPENROUTER_SITE_URL,
                        "X-Title": config.OPENROUTER_APP_NAME,
                    }
                self._client = OpenAI(**kwargs)
        return self._client

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: int = 600,
        json_mode: bool = False,
    ) -> LLMResult:
        """Send a chat completion. ``messages`` is a list of {role, content}."""
        if not self.configured:
            raise LLMNotConfiguredError("No LLM provider is configured.")

        client = self._get_client()
        model = self._resolved.model
        
        # Anthropic has a different API structure
        if self._resolved.name == "anthropic":
            return self._chat_anthropic(client, messages, temperature, max_tokens)
        
        # OpenAI-compatible providers
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # T-3: bound each LLM call. The caller handles the fallback.
            "timeout": config.TOOL_TIMEOUT_SECONDS,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        completion = client.chat.completions.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = (completion.choices[0].message.content or "").strip()
        return LLMResult(
            text=text, model=model, latency_ms=latency_ms, provider=self._resolved.name
        )
    
    def _chat_anthropic(self, client, messages: list[dict], temperature: float, max_tokens: int) -> LLMResult:
        """Handle Anthropic-specific API call."""
        # Anthropic requires system messages to be separate
        system_msg = None
        anthropic_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg.get("content", "")
            else:
                anthropic_messages.append({
                    "role": msg.get("role"),
                    "content": msg.get("content", "")
                })
        
        kwargs = {
            "model": self._resolved.model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": config.TOOL_TIMEOUT_SECONDS,
        }
        if system_msg:
            kwargs["system"] = system_msg
        
        start = time.perf_counter()
        response = client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        
        text = ""
        if response.content and len(response.content) > 0:
            text = response.content[0].text.strip()
        
        return LLMResult(
            text=text,
            model=self._resolved.model,
            latency_ms=latency_ms,
            provider=self._resolved.name
        )

    def structured(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 600,
    ) -> tuple[dict, LLMResult]:
        """Request JSON output and return (parsed_dict, LLMResult).

        Uses JSON mode and a tolerant extractor as a fallback so a stray prose
        wrapper around the JSON does not break parsing (handles providers/models
        that don't fully honour JSON mode).
        """
        result = self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
        )
        parsed = _extract_json(result.text)
        return parsed, result


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from a model response."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


# Convenience singleton.
llm_service = LLMService()
