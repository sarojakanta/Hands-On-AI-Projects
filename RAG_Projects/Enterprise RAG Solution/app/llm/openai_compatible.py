"""
Provider for any server that implements the OpenAI Chat Completions API.

Groq, OpenAI and user-defined providers (Together, Mistral, vLLM, LM Studio, ...) are all
instances or thin subclasses of this class. It talks to ``{base_url}/chat/completions``
and ``{base_url}/models``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import (
    ChatMessage,
    GenerationParams,
    HttpLLMProvider,
    LLMResult,
    ModelInfo,
    StreamEvent,
)
from app.llm.errors import InvalidRequestError, InvalidResponseError, ProviderUnavailableError
from app.llm.http_errors import extract_error
from app.llm.sse import iter_sse, parse_json_object
from app.llm.utils import normalize_finish_reason, usage_from_counts


class OpenAICompatibleProvider(HttpLLMProvider):
    kind = "openai_compatible"

    def __init__(
        self,
        *,
        requires_api_key: bool = True,
        allows_base_url_override: bool = False,
        strict_model_check: bool = True,
        include_stream_usage: bool = False,
        max_tokens_field: str = "max_tokens",
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.requires_api_key = requires_api_key
        self.allows_base_url_override = allows_base_url_override
        self.strict_model_check = strict_model_check
        self._include_stream_usage = include_stream_usage
        self._max_tokens_field = max_tokens_field
        self._extra_headers = dict(extra_headers or {})

    def _auth_headers(self) -> dict[str, str]:
        headers = dict(self._extra_headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ---- models ------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        self._require_ready()
        data = await self._retrying(lambda: self._request_json("GET", "/models"))
        items = data.get("data", data.get("models"))
        if not isinstance(items, list):
            raise InvalidResponseError(f"{self.name} returned an unexpected model list.", provider=self.name)
        models = [
            ModelInfo(id=str(item["id"]))
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
        return sorted(models, key=lambda m: m.id)

    # ---- request building ----------------------------------------------------

    def _payload(self, messages: list[ChatMessage], params: GenerationParams, *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": params.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            self._max_tokens_field: params.max_tokens,
            "stream": stream,
        }
        temperature = self._temperature_for(params.model, params.temperature)
        if temperature is not None:
            body["temperature"] = temperature
        if stream and self._include_stream_usage:
            body["stream_options"] = {"include_usage": True}
        return body

    # ---- non-streaming -------------------------------------------------------

    async def _generate_once(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        while True:
            try:
                data = await self._request_json(
                    "POST",
                    "/chat/completions",
                    json_body=self._payload(messages, params, stream=False),
                    model=params.model,
                    timeout=params.timeout,
                )
                break
            except InvalidRequestError as exc:
                if self._should_drop_temperature(exc, params.model):
                    continue
                raise
        return self._parse_completion(data, params)

    def _parse_completion(self, data: dict[str, Any], params: GenerationParams) -> LLMResult:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise InvalidResponseError(
                f"{self.name} returned no completion choices.", provider=self.name, model=params.model
            )
        choice = choices[0]
        message = choice.get("message") or {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResult(
            text=message.get("content") or "",
            provider=self.name,
            model=str(data.get("model") or params.model),
            usage=usage_from_counts(usage.get("prompt_tokens"), usage.get("completion_tokens")),
            finish_reason=normalize_finish_reason(choice.get("finish_reason")),
        )

    # ---- streaming -------------------------------------------------------------

    async def _stream_once(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        while True:
            try:
                # InvalidRequestError can only come from the HTTP status of the opening request,
                # i.e. before any event was produced, so retrying here is always safe.
                async for event in self._stream_attempt(messages, params):
                    yield event
                return
            except InvalidRequestError as exc:
                if self._should_drop_temperature(exc, params.model):
                    continue
                raise

    async def _stream_attempt(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        lines = self._stream_lines(
            "/chat/completions",
            self._payload(messages, params, stream=True),
            model=params.model,
            timeout=params.timeout,
        )
        usage = None
        finish = None
        # Read to the end of the stream (including the "[DONE]" sentinel) so the connection closes cleanly.
        async for sse in iter_sse(lines):
            data = sse.data.strip()
            if not data or data == "[DONE]":
                continue
            chunk = parse_json_object(data, provider=self.name)
            if "error" in chunk and not chunk.get("choices"):
                message, code = extract_error(data)
                raise ProviderUnavailableError(
                    f"{self.name} reported an error mid-stream: {message or code or 'unknown'}",
                    provider=self.name,
                    model=params.model,
                )
            raw_usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
            if isinstance(raw_usage, dict):
                usage = usage_from_counts(raw_usage.get("prompt_tokens"), raw_usage.get("completion_tokens"))
            for choice in chunk.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content")
                if delta:
                    yield StreamEvent(delta=delta)
                if choice.get("finish_reason"):
                    finish = normalize_finish_reason(choice["finish_reason"])
        yield StreamEvent(done=True, usage=usage, finish_reason=finish)
