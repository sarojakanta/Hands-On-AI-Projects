"""Structured input/output schemas for agent tools.

The LLM only ever supplies the "business" arguments (course name, student
details, etc.). Tenant scoping (institute_id) and the DB session are injected
by the agent runtime, never by the model — so the model cannot cross tenants.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- Inputs ---------------------------------------------------------------
class CourseNameInput(BaseModel):
    course_name: str = Field(..., min_length=1, max_length=200)


class SearchInput(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class CreateLeadInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=7, max_length=20)
    email: str | None = None
    course: str | None = Field(None, max_length=200)


class LeadStatusInput(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)


class FollowUpInput(BaseModel):
    lead_id: int
    preferred_time: str | None = None
    notes: str | None = None


class EscalateInput(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500)


# --- Output ---------------------------------------------------------------
class ToolResult(BaseModel):
    """Uniform tool result.

    ``ok`` indicates success. ``message`` is a human-readable summary the
    agent can surface directly. ``data`` holds structured details.
    """

    ok: bool
    message: str
    data: dict | None = None
