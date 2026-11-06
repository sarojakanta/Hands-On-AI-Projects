"""Course and batch schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    timing: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    seats_total: int
    seats_available: int
    is_active: bool


class CourseBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    fee: float = Field(0.0, ge=0)
    currency: str = "INR"
    duration: str | None = None
    eligibility: str | None = None
    mode: str | None = None


class CourseCreate(CourseBase):
    """Payload for creating a course (admin)."""


class CourseUpdate(BaseModel):
    """Partial update for a course (admin)."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    fee: float | None = Field(None, ge=0)
    currency: str | None = None
    duration: str | None = None
    eligibility: str | None = None
    mode: str | None = None
    is_active: bool | None = None


class CourseOut(CourseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    is_active: bool
    batches: list[BatchOut] = []
