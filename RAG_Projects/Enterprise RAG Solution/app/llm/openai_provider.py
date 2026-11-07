"""OpenAI: the reference implementation of the Chat Completions API."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from app.llm.openai_compatible import OpenAICompatibleProvider

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(OpenAICompatibleProvider):
    """
    Differences from the generic compatible provider:

    * uses ``max_completion_tokens`` (the current OpenAI parameter name),
    * asks for token usage in streamed responses.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        default_model: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="openai",
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            max_tokens_field="max_completion_tokens",
            include_stream_usage=True,
            **kwargs,
        )
