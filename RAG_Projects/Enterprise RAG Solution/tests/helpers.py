"""Test helpers for the LLM layer: scripted HTTP responses and fake providers. No network is used."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.llm.base import (
    ChatMessage,
    ConnectionStatus,
    GenerationParams,
    LLMProvider,
    LLMResult,
    ModelInfo,
    StreamEvent,
)
from app.llm.errors import LLMError

Handler = Callable[[httpx.Request], httpx.Response]

PARAMS = GenerationParams(model="m1", temperature=0.3, max_tokens=50)
MESSAGES = [ChatMessage(role="user", content="Hello")]


# --------------------------------------------------------------------------- HTTP mocking


class Calls:
    """Records every request that reached the mock transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __len__(self) -> int:
        return len(self.requests)

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content or b"null")

    def header(self, name: str, index: int = -1) -> str | None:
        return self.requests[index].headers.get(name)

    def path(self, index: int = -1) -> str:
        return self.requests[index].url.path

    def url(self, index: int = -1) -> str:
        return str(self.requests[index].url)


def mock_client(handler: Handler) -> tuple[httpx.AsyncClient, Calls]:
    calls = Calls()

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.requests.append(request)
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), calls


def scripted(*items: Any) -> Handler:
    """
    Return the given items one per request (the last one repeats).
    An item may be an ``httpx.Response``, an exception (raised), or a zero-argument factory.
    """
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        item = items[min(state["i"], len(items) - 1)]
        state["i"] += 1
        if callable(item) and not isinstance(item, httpx.Response):
            item = item()
        if isinstance(item, Exception):
            raise item
        # httpx mutates responses while processing them, so never hand out the same object twice.
        return httpx.Response(item.status_code, headers=item.headers, content=item.content)

    return handler


def json_response(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers)


def sse_body(events: list[tuple[str | None, Any]]) -> str:
    parts = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        parts.append((f"event: {name}\n" if name else "") + f"data: {payload}\n\n")
    return "".join(parts)


def sse_response(events: list[tuple[str | None, Any]], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=sse_body(events).encode(), headers={"content-type": "text/event-stream"})


def ndjson_response(objects: list[Any], status: int = 200) -> httpx.Response:
    body = "".join(json.dumps(o) + "\n" for o in objects)
    return httpx.Response(status, content=body.encode(), headers={"content-type": "application/x-ndjson"})


class SleepRecorder:
    """Replaces ``asyncio.sleep`` so retry tests run instantly and can assert the delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def collect(stream: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in stream]


async def agen(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


# --------------------------------------------------------------------------- fake provider


class FakeProvider(LLMProvider):
    """A scripted provider for service/API tests (no HTTP)."""

    kind = "fake"

    def __init__(
        self,
        name: str,
        *,
        configured: bool = True,
        default_model: str = "fm",
        error: LLMError | None = None,
        tokens: tuple[str, ...] = ("Hel", "lo"),
        error_after_tokens: LLMError | None = None,
        connection_ok: bool = True,
        allows_override: bool = False,
        reply: str | None = None,
        reply_fn: Callable[[list[ChatMessage]], str] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.reply = reply
        self.reply_fn = reply_fn
        self.finish_reason = finish_reason
        self.name = name
        self.base_url = f"http://{name}.test"
        self.default_model = default_model
        self.allows_base_url_override = allows_override
        self._configured = configured
        self.error = error
        self.tokens = tokens
        self.error_after_tokens = error_after_tokens
        self.connection_ok = connection_ok
        self.calls: list[GenerationParams] = []
        self.last_messages: list[ChatMessage] = []
        self.all_messages: list[list[ChatMessage]] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=self.default_model or "model-x")]

    def _scripted_text(self, messages: list[ChatMessage]) -> str | None:
        if self.reply_fn is not None:
            return self.reply_fn(messages)   # may raise an LLMError to simulate a failing call
        return self.reply

    async def generate(self, messages: list[ChatMessage], params: GenerationParams) -> LLMResult:
        self.calls.append(params)
        self.all_messages.append(messages)
        self.last_messages = messages
        if self.error:
            raise self.error
        text = self._scripted_text(messages)
        return LLMResult(
            text=f"{self.name}:{params.model}" if text is None else text,
            provider=self.name,
            model=params.model,
            finish_reason=self.finish_reason,
        )

    async def stream(self, messages: list[ChatMessage], params: GenerationParams) -> AsyncIterator[StreamEvent]:
        self.calls.append(params)
        self.all_messages.append(messages)
        self.last_messages = messages
        if self.error:
            raise self.error
        text = self._scripted_text(messages)
        tokens = self.tokens if text is None else tuple(text[i : i + 6] for i in range(0, len(text), 6))
        for token in tokens:
            yield StreamEvent(delta=token)
        if self.error_after_tokens:
            raise self.error_after_tokens
        yield StreamEvent(done=True, finish_reason=self.finish_reason or "stop")

    async def test_connection(self, model: str | None = None) -> ConnectionStatus:
        return ConnectionStatus(
            ok=self.connection_ok,
            provider=self.name,
            message="ok" if self.connection_ok else "connection refused",
            error_code=None if self.connection_ok else "provider_unavailable",
        )
