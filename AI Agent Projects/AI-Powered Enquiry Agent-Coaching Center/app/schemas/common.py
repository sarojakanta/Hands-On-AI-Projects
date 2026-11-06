"""Shared/common Pydantic schemas."""
from __future__ import annotations

from pydantic import BaseModel


class Message(BaseModel):
    """Generic message response."""

    detail: str


class HealthResponse(BaseModel):
    """Health-check payload."""

    status: str = "ok"
    app: str
    version: str
    llm_enabled: bool
