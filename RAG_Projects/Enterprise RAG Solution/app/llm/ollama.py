"""
Ollama provider (locally hosted models, no API key).

Uses Ollama's native API:

* ``GET  /api/tags``  - installed models
* ``POST /api/chat``  - chat; streaming responses are newline-delimited JSON, the final
  object has ``done: true`` plus ``prompt_eval_count`` / ``eval_count`` token counts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Mapping
from typing import Any

from app.llm.base import (
    ChatMessage,
    GenerationParams,
    HttpLLMProvider,
    LLMResult,
    ModelInfo,
    StreamEvent,
)
from app.llm.errors import (
    InvalidResponseError,
    LLMError,
    ModelNotFoundError,
    ProviderUnavailableError,
)
from app.llm.sse import iter_ndjson
from app.llm.utils import normalize_finish_reason, usage_from_counts

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaProvider(HttpLLMProvider):
    kind = "ollama"
    requires_api_key = False
    allows_base_url_override = True

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        default_model: str = "",
        keep_alive: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name="ollama",
            base_url=base_url,
            api_key=None,
            default_model=default_model,
            **kwargs,
        )
        self._keep_alive = keep_alive

    # ---- models ------------------------------------------------------------

    @staticmethod
    def _normalise(name: str) -> str:
        """Ollama treats ``llama3.1`` and ``llama3.1:latest`` as the same model."""
        return name if ":" in name else f"{name}:latest"

    def model_matches(self, wanted: str, available_ids: Collection[str]) -> bool:
        return self._normalise(wanted) in {self._normalise(model_id) for model_id in available_ids}

    async def list_models(self) -> list[ModelInfo]:
        data = await self._retrying(lambda: self._request_json("GET", "/api/tags"))
        items = data.get("models")
        if not isinstance(items, list):
            raise InvalidResponseError("Ollama returned an unexpected model list.", provider=self.name)
        models = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if name:
                size = item.get("size")
                models.append(ModelInfo(id=str(name), size_bytes=size if isinstance(size, int) else None))
        return sorted(models, key=lambda m: m.id)

    def _error_from_response(
        self, status: int, headers: Mapping[str, str], body: str, model: str | None
    ) -> LLMError:
        error = super()._error_from_response(status, headers, body, model)
        if isinstance(error, ModelNotFoundError) and model:
            return ModelNotFoundError(
                f"Model '{model}' is not installed on Ollama. Install it with: ollama pull {model}",
                provider=self.name,
                model=model,
                upstream_status=status,
            )
        return error

    # ---- request building ----------------------------------------------------

    def _payload(self, messages: list[ChatMessage], params: GenerationParams, *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": params.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "options": {"temperature": params.temperature, "num_predict": params.max_tokens},
        }
        if self._keep_alive:
            body["keep_alive"] = self._keep_alive
        return body

    # ---- non-streaming -------------------------------------------------------

    async def _generate_once(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        data = await self._request_json(
            "POST",
            "/api/chat",
            json_body=self._payload(messages, params, stream=False),
            model=params.model,
            timeout=params.timeout,
        )
        if data.get("error"):
            raise ProviderUnavailableError(
                f"Ollama reported an error: {data['error']}", provider=self.name, model=params.model
            )
        message = data.get("message") or {}
        return LLMResult(
            text=message.get("content") or "",
            provider=self.name,
            model=str(data.get("model") or params.model),
            usage=usage_from_counts(data.get("prompt_eval_count"), data.get("eval_count")),
            finish_reason=normalize_finish_reason(data.get("done_reason")),
        )

    # ---- streaming -------------------------------------------------------------

    async def _stream_once(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        lines = self._stream_lines(
            "/api/chat",
            self._payload(messages, params, stream=True),
            model=params.model,
            timeout=params.timeout,
        )
        usage = None
        finish = None
        async for obj in iter_ndjson(lines, provider=self.name):
            if obj.get("error"):
                raise ProviderUnavailableError(
                    f"Ollama reported an error mid-stream: {obj['error']}",
                    provider=self.name,
                    model=params.model,
                )
            content = (obj.get("message") or {}).get("content")
            if content:
                yield StreamEvent(delta=content)
            if obj.get("done"):
                usage = usage_from_counts(obj.get("prompt_eval_count"), obj.get("eval_count"))
                finish = normalize_finish_reason(obj.get("done_reason"))
        yield StreamEvent(done=True, usage=usage, finish_reason=finish)
