"""Lead and follow-up schemas."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_PHONE_RE = re.compile(r"^[+()\-\s0-9]{7,20}$")


class LeadCreate(BaseModel):
    """Payload to register a prospective student enquiry."""

    name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=7, max_length=20)
    email: EmailStr | None = None
    course_interest: str | None = Field(None, max_length=200)
    course_id: int | None = None
    notes: str | None = None
    source: str = "chat_widget"

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not _PHONE_RE.match(v):
            raise ValueError("Invalid phone number format.")
        return v

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


class LeadUpdate(BaseModel):
    """Admin update for a lead (status/notes)."""

    status: str | None = None
    notes: str | None = None
    course_id: int | None = None


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    email: str | None = None
    course_interest: str | None = None
    course_id: int | None = None
    status: str
    source: str
    notes: str | None = None
    created_at: datetime


class FollowUpCreate(BaseModel):
    lead_id: int
    preferred_time: datetime | None = None
    notes: str | None = None


class FollowUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    preferred_time: datetime | None = None
    notes: str | None = None
    status: str
    created_at: datetime
