"""Groq: an OpenAI-compatible API with its own base URL, rate limits and 429/``retry-after`` semantics."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from app.llm.openai_compatible import OpenAICompatibleProvider

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        base_url: str = DEFAULT_GROQ_BASE_URL,
        default_model: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="groq",
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            **kwargs,
        )
