"""RAG question answering (JSON or Server-Sent Events) and conversation history."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_rag_pipeline
from app.api.schemas.chat import (
    ChatRequest,
    answer_to_dict,
    conversation_to_dict,
    source_to_dict,
)
from app.core.errors import AppError
from app.rag.pipeline import (
    AnswerDone,
    GenerationStart,
    RagEvent,
    RagPipeline,
    RetrievalEvent,
    TokenEvent,
)

logger = logging.getLogger("app.api")

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_event(item: RagEvent) -> str:
    if isinstance(item, RetrievalEvent):
        return _sse(
            "retrieval",
            {
                "conversation_id": item.conversation_id,
                "standalone_query": item.standalone_query,
                "rewrite_failed": item.rewrite_failed,
                "found_context": item.found_context,
                "sources": [source_to_dict(s, with_text=False) for s in item.sources],
                "timings_ms": item.timings_ms,
            },
        )
    if isinstance(item, GenerationStart):
        return _sse(
            "start",
            {
                "provider": item.provider,
                "model": item.model,
                "used_fallback": item.used_fallback,
                "fallback_from": item.fallback_from,
                "fallback_reason": item.fallback_reason,
            },
        )
    if isinstance(item, TokenEvent):
        return _sse("token", {"delta": item.delta})
    assert isinstance(item, AnswerDone)
    # The final event is authoritative: its text has invalid citation markers removed.
    return _sse("done", answer_to_dict(item.answer))


@router.post("/chat")
async def chat(body: ChatRequest, pipeline: RagPipeline = Depends(get_rag_pipeline)):
    """
    Answer a question from the indexed documents.

    * ``stream: false`` returns one JSON object (answer, citations, retrieved passages, timings).
    * ``stream: true`` returns ``text/event-stream`` with ``retrieval``, ``start``, ``token``,
      ``done`` and ``error`` events. Clients should replace the streamed text with ``done.answer``.

    Pass the returned ``conversation_id`` back to ask follow-up questions.
    """
    options = body.to_options()

    if not body.stream:
        answer = await pipeline.answer(body.question, conversation_id=body.conversation_id, options=options)
        return JSONResponse(answer_to_dict(answer), headers={"X-Conversation-Id": answer.conversation_id})

    iterator = pipeline.stream(body.question, conversation_id=body.conversation_id, options=options)

    # Advance until generation has really started (or the answer is final). Anything that fails up to
    # that point - unknown conversation, index not ready, provider down - becomes a proper HTTP error.
    prefetched: list[RagEvent] = []
    try:
        while True:
            item = await iterator.__anext__()
            prefetched.append(item)
            if isinstance(item, (GenerationStart, AnswerDone)):
                break
    except StopAsyncIteration:
        pass

    async def event_stream() -> AsyncIterator[str]:
        try:
            for item in prefetched:
                yield _format_event(item)
            async for item in iterator:
                yield _format_event(item)
        except AppError as exc:
            yield _sse("error", exc.to_payload()["error"])
        except Exception:
            logger.exception("Unexpected error while streaming an answer")
            yield _sse("error", {"code": "internal_error", "message": "Unexpected error while streaming."})

    conversation_id = next((i.conversation_id for i in prefetched if isinstance(i, RetrievalEvent)), "")
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": conversation_id,
        },
    )


@router.get("/conversations")
async def list_conversations(limit: int = 100, pipeline: RagPipeline = Depends(get_rag_pipeline)):
    """Most-recently-used first. Empty conversations are omitted. No message bodies, just titles and counts."""
    limit = max(1, min(limit, 500))
    summaries = pipeline.conversations.list(limit)
    return {
        "conversations": [
            {
                "id": s.id,
                "title": s.title,
                "message_count": s.message_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ]
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, pipeline: RagPipeline = Depends(get_rag_pipeline)):
    return conversation_to_dict(pipeline.conversations.get(conversation_id))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, pipeline: RagPipeline = Depends(get_rag_pipeline)):
    """Clear a conversation's history (the next question with this id is rejected; start a new one)."""
    if not pipeline.conversations.delete(conversation_id):
        # Reuse the store's error for a consistent 404 envelope.
        pipeline.conversations.get(conversation_id)
    return {"deleted": True, "conversation_id": conversation_id}
