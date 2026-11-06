"""StudentLead and FollowUp models."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.institute import Institute
    from app.models.conversation import Conversation


class StudentLead(Base, TimestampMixin):
    """A prospective student enquiry registered as a lead."""

    __tablename__ = "student_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    course_interest: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # new / contacted / enrolled / closed
    status: Mapped[str] = mapped_column(String(40), default="new", index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="chat_widget", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    institute: Mapped["Institute"] = relationship(back_populates="leads")
    course: Mapped["Course | None"] = relationship(back_populates="leads")
    followups: Mapped[list["FollowUp"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="lead")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<StudentLead id={self.id} name={self.name!r} phone={self.phone!r}>"


class FollowUp(Base, TimestampMixin):
    """A follow-up request for institute staff to contact a lead."""

    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("student_leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    preferred_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending / scheduled / done / cancelled
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)

    lead: Mapped["StudentLead"] = relationship(back_populates="followups")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<FollowUp id={self.id} lead_id={self.lead_id} status={self.status!r}>"
