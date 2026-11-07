"""Schemas and serialisation helpers for the RAG chat endpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field

from app.rag.conversations import Conversation
from app.rag.pipeline import RagOptions
from app.rag.types import ContextItem, RagAnswer

MAX_QUESTION_LENGTH = 4000   # hard cap at the API edge; the configured limit is enforced by the pipeline


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    top_k: int | None = Field(default=None, ge=1, le=20)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rewrite: bool | None = None

    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    allow_fallback: bool | None = None

    stream: bool = False

    def to_options(self) -> RagOptions:
        return RagOptions(
            top_k=self.top_k,
            min_score=self.min_score,
            rewrite=self.rewrite,
            provider=self.provider,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            allow_fallback=self.allow_fallback,
        )


def source_to_dict(item: ContextItem, *, cited: bool | None = None, with_text: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "number": item.number,
        "document": item.chunk.source,
        "chunk_id": item.chunk.chunk_id,
        "document_id": item.chunk.document_id,
        "version": item.chunk.version,
        "page": item.chunk.page,
        "score": round(item.chunk.score, 4),
    }
    if with_text:
        data["text"] = item.chunk.text
    if cited is not None:
        data["cited"] = cited
    return data


def answer_to_dict(answer: RagAnswer) -> dict[str, Any]:
    cited_numbers = {c.number for c in answer.citations}
    usage = answer.usage
    return {
        "conversation_id": answer.conversation_id,
        "message_id": answer.message_id,
        "question": answer.question,
        "standalone_query": answer.standalone_query,
        "answer": answer.answer,
        "found_in_documents": answer.found_in_documents,
        "citations": [{**asdict(c), "score": round(c.score, 4)} for c in answer.citations],
        "retrieved": [source_to_dict(item, cited=item.number in cited_numbers) for item in answer.retrieved],
        "provider": answer.provider,
        "model": answer.model,
        "finish_reason": answer.finish_reason,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
        "used_fallback": answer.used_fallback,
        "fallback_from": answer.fallback_from,
        "fallback_reason": answer.fallback_reason,
        "attempts": answer.attempts,
        "rewrite_failed": answer.rewrite_failed,
        "timings_ms": answer.timings_ms,
        "warnings": answer.warnings,
    }


def conversation_to_dict(conversation: Conversation) -> dict[str, Any]:
    messages = []
    for message in conversation.messages:
        entry: dict[str, Any] = {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at,
        }
        if message.role == "assistant":
            entry["citations"] = message.metadata.get("citations", [])
            entry["found_in_documents"] = message.metadata.get("found_in_documents")
        messages.append(entry)
    return {"id": conversation.id, "created_at": conversation.created_at, "messages": messages}
