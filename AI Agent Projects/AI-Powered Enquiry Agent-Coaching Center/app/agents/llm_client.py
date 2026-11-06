"""LLM client abstraction with pluggable providers.

One interface, several backends:

* ``OpenAICompatibleClient`` — any OpenAI-compatible chat API. Covers
  **openai**, **groq**, **openrouter** and **ollama** (they differ only by
  base URL, key and model). This is the single code path for four providers.
* ``AnthropicClient`` — Anthropic Claude via its native SDK (system prompt is
  passed separately, which maps cleanly onto our ``complete(system, messages)``
  signature).
* ``MockLLMClient`` — deterministic, offline, free responder used as the
  default fallback and in tests.

The active client is chosen by ``settings.resolve_llm_provider()``. The default
provider is **ollama** (local, private, free). If a provider can't be built
(missing SDK/key), we log a warning and fall back to the mock so the app always
runs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import get_settings
from app.config.logging_config import get_logger

logger = get_logger(__name__)

# Default base URLs per provider (used when the *_BASE_URL env var is blank).
_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


class LLMClient(ABC):
    """Abstract chat LLM client."""

    @abstractmethod
    def complete(self, system: str, messages: list[dict], **kwargs) -> str:
        """Return the assistant's reply text.

        ``messages`` is a list of {"role": "user"|"assistant", "content": str}.
        """


class OpenAICompatibleClient(LLMClient):
    """Chat client for any OpenAI-compatible API (openai/groq/openrouter/ollama)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float,
        extra_headers: dict | None = None,
        provider: str = "openai",
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._extra_headers = extra_headers or None
        self._provider = provider

    def complete(self, system: str, messages: list[dict], **kwargs) -> str:
        chat = [{"role": "system", "content": system}, *messages]
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=chat,
            temperature=kwargs.get("temperature", self._temperature),
            max_tokens=kwargs.get("max_tokens", 500),
            extra_headers=self._extra_headers,
        )
        return (resp.choices[0].message.content or "").strip()


class AnthropicClient(LLMClient):
    """Chat client for Anthropic Claude (native SDK)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float,
    ) -> None:
        import anthropic

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self._model = model
        self._temperature = temperature

    def complete(self, system: str, messages: list[dict], **kwargs) -> str:
        # Anthropic takes the system prompt as a top-level arg and only
        # user/assistant turns in `messages`.
        convo = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        base_kwargs = {
            "model": self._model,
            "system": system,
            "messages": convo,
            "max_tokens": kwargs.get("max_tokens", 500),
        }
        temperature = kwargs.get("temperature", self._temperature)
        try:
            resp = self._client.messages.create(temperature=temperature, **base_kwargs)
        except Exception as exc:  # noqa: BLE001
            # Newer Claude models reject `temperature` ("deprecated for this
            # model"). Retry once without it.
            if "temperature" in str(exc).lower():
                resp = self._client.messages.create(**base_kwargs)
            else:
                raise
        parts = [block.text for block in resp.content if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()


class MockLLMClient(LLMClient):
    """Deterministic offline responder.

    The agent passes grounding context and/or tool results inside the last
    user message using lightweight tags:

        [GROUNDING]...retrieved chunks...[/GROUNDING]
        [TOOL_RESULT]...plain text summary...[/TOOL_RESULT]
        [QUESTION]...the student's question...[/QUESTION]

    The mock composes a reply from whichever tags are present. If grounding is
    present it answers from it; otherwise it admits it cannot find the
    information and offers human help — mirroring the real prompt's rules.
    """

    def complete(self, system: str, messages: list[dict], **kwargs) -> str:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break

        tool_result = _extract(last_user, "TOOL_RESULT")
        grounding = _extract(last_user, "GROUNDING")

        if tool_result:
            return tool_result.strip()

        if grounding:
            snippet = grounding.strip()
            if len(snippet) > 600:
                snippet = snippet[:600].rsplit(" ", 1)[0] + "..."
            return (
                "Here's what I found based on our information:\n\n"
                f"{snippet}\n\n"
                "Would you like me to help with anything else, such as "
                "registering an enquiry or connecting you with a counsellor?"
            )

        return (
            "I'm not certain about that from the information I have. I don't "
            "want to give you incorrect details, so I can connect you with one "
            "of our counsellors who can help. Would you like me to do that? "
            "You can also ask me about our courses, fees, batches, eligibility, "
            "or the admission process."
        )


def _extract(text: str, tag: str) -> str | None:
    open_t, close_t = f"[{tag}]", f"[/{tag}]"
    if open_t in text and close_t in text:
        return text.split(open_t, 1)[1].split(close_t, 1)[0]
    return None


def _base_url(provider: str, configured: str) -> str:
    return configured.strip() if configured and configured.strip() else _DEFAULT_BASE_URLS.get(provider, "")


def _build_client(provider: str, settings) -> LLMClient:
    """Construct the concrete client for a resolved provider name."""
    temp = settings.llm_temperature

    if provider == "mock":
        return MockLLMClient()

    if provider == "anthropic":
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            temperature=temp,
        )

    # OpenAI-compatible providers.
    if provider == "openai":
        return OpenAICompatibleClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=_base_url("openai", settings.openai_base_url),
            temperature=temp,
            provider="openai",
        )
    if provider == "groq":
        return OpenAICompatibleClient(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=_base_url("groq", settings.groq_base_url),
            temperature=temp,
            provider="groq",
        )
    if provider == "openrouter":
        headers = {
            "HTTP-Referer": settings.openrouter_site_url,
            "X-Title": settings.openrouter_app_name,
        }
        return OpenAICompatibleClient(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            base_url=_base_url("openrouter", settings.openrouter_base_url),
            temperature=temp,
            extra_headers=headers,
            provider="openrouter",
        )
    if provider == "ollama":
        return OpenAICompatibleClient(
            api_key=settings.ollama_api_key or "ollama",
            model=settings.ollama_model,
            base_url=_base_url("ollama", settings.ollama_base_url),
            temperature=temp,
            provider="ollama",
        )

    # Unknown -> mock.
    return MockLLMClient()


_cached_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the active LLM client based on configuration (cached).

    Falls back to the mock (with a warning) if the chosen provider can't be
    constructed — e.g. its SDK isn't installed or a key is missing — so the
    app always keeps running.
    """
    global _cached_client
    if _cached_client is not None:
        return _cached_client

    settings = get_settings()
    provider = settings.resolve_llm_provider()
    try:
        _cached_client = _build_client(provider, settings)
        if provider == "mock":
            logger.info("Using MockLLMClient (offline / deterministic).")
        else:
            logger.info("Using LLM provider '%s'.", provider)
    except Exception as exc:  # noqa: BLE001 - never let LLM setup crash the app
        logger.warning(
            "Could not initialise provider '%s' (%s); falling back to mock.",
            provider,
            exc,
        )
        _cached_client = MockLLMClient()
    return _cached_client


def reset_llm_cache() -> None:
    global _cached_client
    _cached_client = None
