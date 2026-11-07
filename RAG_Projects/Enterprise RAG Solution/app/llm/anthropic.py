"""
Anthropic Claude provider (Messages API).

* ``POST /v1/messages`` - non-streaming and SSE streaming (events: ``message_start``,
  ``content_block_delta``, ``message_delta``, ``message_stop``, ``ping``, ``error``;
  unknown events are ignored as the API documents they may be added).
* ``GET  /v1/models``   - paginated model list.

No default model is hardcoded: Claude model names are retired over time. Choose one from
the provider's model list or set ``ANTHROPIC_DEFAULT_MODEL``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

from app.llm.base import (
    ChatMessage,
    GenerationParams,
    HttpLLMProvider,
    LLMResult,
    ModelInfo,
    StreamEvent,
)
from app.llm.errors import InvalidRequestError, InvalidResponseError, LLMError
from app.llm.http_errors import map_http_error
from app.llm.sse import iter_sse, parse_json_object
from app.llm.utils import normalize_finish_reason, usage_from_counts

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
MODEL_PAGE_SIZE = 100
MAX_MODEL_PAGES = 10

# Mid-stream ``error`` events carry a type but no HTTP status; map them to the equivalent status.
_ERROR_TYPE_TO_STATUS = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
    "overloaded_error": 529,
}


class AnthropicProvider(HttpLLMProvider):
    kind = "anthropic"

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        api_version: str = DEFAULT_ANTHROPIC_VERSION,
        default_model: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="anthropic",
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            **kwargs,
        )
        self._api_version = api_version

    def _auth_headers(self) -> dict[str, str]:
        headers = {"anthropic-version": self._api_version}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    # ---- models ------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        self._require_ready()
        models: list[ModelInfo] = []
        after_id: str | None = None
        for _ in range(MAX_MODEL_PAGES):
            path = f"/v1/models?limit={MODEL_PAGE_SIZE}"
            if after_id:
                path += f"&after_id={quote(after_id, safe='')}"
            data = await self._retrying(lambda p=path: self._request_json("GET", p))
            items = data.get("data")
            if not isinstance(items, list):
                raise InvalidResponseError("Anthropic returned an unexpected model list.", provider=self.name)
            models.extend(
                ModelInfo(id=str(item["id"]), display_name=item.get("display_name"))
                for item in items
                if isinstance(item, dict) and item.get("id")
            )
            last_id = data.get("last_id")
            if data.get("has_more") and last_id:
                after_id = str(last_id)
            else:
                break
        return models   # API order: most recently released first

    # ---- request building ----------------------------------------------------

    def _payload(self, messages: list[ChatMessage], params: GenerationParams, *, stream: bool) -> dict[str, Any]:
        system_parts = [m.content for m in messages if m.role == "system"]
        conversation = [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]
        if not conversation:
            raise InvalidRequestError(
                "Anthropic requires at least one user message.", provider=self.name, model=params.model
            )
        body: dict[str, Any] = {
            "model": params.model,
            "max_tokens": params.max_tokens,
            "messages": conversation,
            "stream": stream,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        # Anthropic accepts temperature in [0, 1] only.
        temperature = self._temperature_for(params.model, min(max(params.temperature, 0.0), 1.0))
        if temperature is not None:
            body["temperature"] = temperature
        return body

    # ---- non-streaming -------------------------------------------------------

    async def _generate_once(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        while True:
            try:
                data = await self._request_json(
                    "POST",
                    "/v1/messages",
                    json_body=self._payload(messages, params, stream=False),
                    model=params.model,
                    timeout=params.timeout,
                )
                break
            except InvalidRequestError as exc:
                if self._should_drop_temperature(exc, params.model):
                    continue
                raise
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise InvalidResponseError(
                "Anthropic returned a response without content.", provider=self.name, model=params.model
            )
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResult(
            text=text,
            provider=self.name,
            model=str(data.get("model") or params.model),
            usage=usage_from_counts(usage.get("input_tokens"), usage.get("output_tokens")),
            finish_reason=normalize_finish_reason(data.get("stop_reason")),
        )

    # ---- streaming -------------------------------------------------------------

    async def _stream_once(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        while True:
            try:
                async for event in self._stream_attempt(messages, params):
                    yield event
                return
            except InvalidRequestError as exc:
                if self._should_drop_temperature(exc, params.model):
                    continue
                raise

    def _stream_error(self, payload: dict[str, Any], model: str) -> LLMError:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        status = _ERROR_TYPE_TO_STATUS.get(str(error.get("type", "")), 500)
        return map_http_error(
            provider=self.name, model=model, status=status, headers={}, body=json.dumps(payload)
        )

    async def _stream_attempt(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        lines = self._stream_lines(
            "/v1/messages",
            self._payload(messages, params, stream=True),
            model=params.model,
            timeout=params.timeout,
        )
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        finish: str | None = None

        async for sse in iter_sse(lines):
            if not sse.data.strip():
                continue
            payload = parse_json_object(sse.data, provider=self.name)
            kind = payload.get("type") or sse.event

            if kind == "message_start":
                usage = (payload.get("message") or {}).get("usage") or {}
                prompt_tokens = usage.get("input_tokens", prompt_tokens)
            elif kind == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield StreamEvent(delta=delta["text"])
            elif kind == "message_delta":
                finish = normalize_finish_reason((payload.get("delta") or {}).get("stop_reason")) or finish
                usage = payload.get("usage") or {}
                completion_tokens = usage.get("output_tokens", completion_tokens)
                prompt_tokens = usage.get("input_tokens", prompt_tokens)
            elif kind == "error":
                raise self._stream_error(payload, params.model)
            # ping, content_block_start/stop, message_stop and unknown events need no action.

        yield StreamEvent(
            done=True,
            usage=usage_from_counts(prompt_tokens, completion_tokens),
            finish_reason=finish,
        )
