"""Course and Batch models."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.institute import Institute
    from app.models.lead import StudentLead


class Course(Base, TimestampMixin):
    """A course offered by an institute."""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    duration: Mapped[str | None] = mapped_column(String(80), nullable=True)
    eligibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(40), nullable=True)  # online/offline/hybrid
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    institute: Mapped["Institute"] = relationship(back_populates="courses")
    batches: Mapped[list["Batch"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    leads: Mapped[list["StudentLead"]] = relationship(back_populates="course")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Course id={self.id} name={self.name!r}>"


class Batch(Base, TimestampMixin):
    """A scheduled batch for a course."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timing: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seats_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seats_available: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    course: Mapped["Course"] = relationship(back_populates="batches")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Batch id={self.id} name={self.name!r}>"
