"""Conversation, message and escalation schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    status: str
    lead_id: int | None = None
    created_at: datetime
    messages: list[ChatMessageOut] = []


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    status: str
    lead_id: int | None = None
    created_at: datetime


class ChatRequest(BaseModel):
    """Public chat request from the widget.

    The maximum message length is configurable via ``MAX_CHAT_MESSAGE_CHARS``
    (enforced in a validator so the limit can change without code edits).
    """

    message: str = Field(..., min_length=1)
    session_id: str | None = Field(None, max_length=64)

    @field_validator("message")
    @classmethod
    def _within_max_length(cls, v: str) -> str:
        max_chars = get_settings().max_chat_message_chars
        if len(v) > max_chars:
            raise ValueError(f"message must be at most {max_chars} characters")
        return v


class ChatSource(BaseModel):
    """A grounding source cited by the agent."""

    title: str
    snippet: str | None = None


class ChatResponse(BaseModel):
    """Public chat response to the widget."""

    session_id: str
    reply: str
    intent: str | None = None
    escalated: bool = False
    sources: list[ChatSource] = []
    collected_lead: bool = False


class EscalationCreate(BaseModel):
    session_id: str
    reason: str = Field(..., min_length=1)


class EscalationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    reason: str
    status: str
    created_at: datetime
