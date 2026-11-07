"""LLM provider endpoints: discovery, connection tests, runtime configuration and a diagnostic completion."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_llm_service
from app.api.schemas.llm import CompleteRequest, ConnectionTestRequest
from app.core.errors import AppError
from app.llm.base import StreamEvent
from app.llm.service import (
    LLMConfigUpdate,
    LLMService,
    StreamItem,
    StreamStart,
    result_to_dict,
)

logger = logging.getLogger("app.api")

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])


# --------------------------------------------------------------------------- providers


@router.get("/providers")
async def list_providers(service: LLMService = Depends(get_llm_service)) -> dict[str, Any]:
    """All known providers with their (non-secret) status, plus the active configuration."""
    return {"providers": service.registry.list_info(), "active": service.config.model_dump()}


@router.get("/providers/{name}/models")
async def list_models(name: str, service: LLMService = Depends(get_llm_service)) -> dict[str, Any]:
    provider = service.registry.get(name)
    models = await provider.list_models()
    return {"provider": provider.name, "models": [asdict(m) for m in models]}


@router.post("/providers/{name}/test")
async def test_provider(
    name: str,
    body: ConnectionTestRequest | None = None,
    service: LLMService = Depends(get_llm_service),
) -> dict[str, Any]:
    """
    Check reachability and credentials without generating text. A failed check is a normal
    result (HTTP 200 with ``ok: false``), not an error of this endpoint.
    """
    status = await service.test_provider(name, body.model if body else None)
    return asdict(status)


# --------------------------------------------------------------------------- configuration


@router.get("/config")
async def get_config(service: LLMService = Depends(get_llm_service)) -> dict[str, Any]:
    return service.config.model_dump()


@router.post("/config/reset")
async def reset_config(service: LLMService = Depends(get_llm_service)) -> dict[str, Any]:
    """Forget the saved configuration and return to the values in the server's .env."""
    return service.reset_config().model_dump()


@router.put("/config")
async def update_config(
    update: LLMConfigUpdate,
    validate: bool = Query(
        default=True,
        description="Test the connection of the new provider(s) before activating them.",
    ),
    service: LLMService = Depends(get_llm_service),
) -> dict[str, Any]:
    config = await service.update_config(update, validate=validate)
    return config.model_dump()


# --------------------------------------------------------------------------- diagnostic completion


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_item(item: StreamItem) -> str | None:
    if isinstance(item, StreamStart):
        return _sse("start", asdict(item))
    assert isinstance(item, StreamEvent)
    if item.done:
        usage = item.usage
        return _sse(
            "done",
            {
                "finish_reason": item.finish_reason,
                "usage": None
                if usage is None
                else {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                },
            },
        )
    return _sse("token", {"delta": item.delta}) if item.delta else None


@router.post("/complete")
async def complete(body: CompleteRequest, service: LLMService = Depends(get_llm_service)):
    """
    Run a prompt through the selected provider. Used to verify provider setup; the RAG
    question-answering endpoints build on the same service.

    With ``stream: true`` the response is ``text/event-stream`` with ``start``, ``token``,
    ``done`` and ``error`` events.
    """
    kwargs = {
        "provider": body.provider,
        "model": body.model,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "allow_fallback": body.allow_fallback,
    }
    messages = body.to_messages()

    if not body.stream:
        result = await service.generate(messages, **kwargs)
        return JSONResponse(result_to_dict(result))

    iterator = service.stream(messages, **kwargs)
    # Pull the first item before starting the response so failures that happen before any output
    # (not configured, unreachable, rate limited, ...) return a proper HTTP error status.
    try:
        first: StreamItem | None = await iterator.__anext__()
    except StopAsyncIteration:
        first = None

    async def event_stream() -> AsyncIterator[str]:
        try:
            if first is not None:
                chunk = _format_item(first)
                if chunk:
                    yield chunk
            async for item in iterator:
                chunk = _format_item(item)
                if chunk:
                    yield chunk
        except AppError as exc:
            yield _sse("error", exc.to_payload()["error"])
        except Exception:
            logger.exception("Unexpected error while streaming a completion")
            yield _sse("error", {"code": "internal_error", "message": "Unexpected error while streaming."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
