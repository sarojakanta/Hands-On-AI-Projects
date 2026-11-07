"""Schemas for the LLM provider endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.llm.base import ChatMessage

MAX_TEXT_LENGTH = 8000
MAX_MESSAGES = 20


class MessageIn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ConnectionTestRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)


class CompleteRequest(BaseModel):
    """
    Diagnostic completion request: send either ``prompt`` (optionally with ``system``) or a full
    ``messages`` list. Provider/model/sampling fields override the active configuration for this
    request only.
    """

    prompt: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_LENGTH)
    system: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_LENGTH)
    messages: list[MessageIn] | None = Field(default=None, min_length=1, max_length=MAX_MESSAGES)

    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    allow_fallback: bool | None = None
    stream: bool = False

    @model_validator(mode="after")
    def _prompt_or_messages(self) -> "CompleteRequest":
        if (self.prompt is None) == (self.messages is None):
            raise ValueError("Provide exactly one of 'prompt' or 'messages'.")
        if self.messages is not None and self.system is not None:
            raise ValueError("'system' can only be combined with 'prompt'; put system text in 'messages' instead.")
        return self

    def to_messages(self) -> list[ChatMessage]:
        if self.messages is not None:
            return [ChatMessage(role=m.role, content=m.content) for m in self.messages]
        messages: list[ChatMessage] = []
        if self.system:
            messages.append(ChatMessage(role="system", content=self.system))
        messages.append(ChatMessage(role="user", content=self.prompt or ""))
        return messages
