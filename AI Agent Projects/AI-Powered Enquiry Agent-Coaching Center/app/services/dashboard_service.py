"""Dashboard aggregation queries (tenant-scoped)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Conversation,
    Escalation,
    FollowUp,
    StudentLead,
)


def get_stats(db: Session, institute_id: int) -> dict:
    """Return headline dashboard counters for an institute."""
    total_enquiries = db.scalar(
        select(func.count(StudentLead.id)).where(
            StudentLead.institute_id == institute_id
        )
    )
    new_leads = db.scalar(
        select(func.count(StudentLead.id)).where(
            StudentLead.institute_id == institute_id,
            StudentLead.status == "new",
        )
    )
    pending_followups = db.scalar(
        select(func.count(FollowUp.id)).where(
            FollowUp.institute_id == institute_id,
            FollowUp.status == "pending",
        )
    )
    open_escalations = db.scalar(
        select(func.count(Escalation.id)).where(
            Escalation.institute_id == institute_id,
            Escalation.status == "open",
        )
    )
    total_conversations = db.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.institute_id == institute_id
        )
    )
    return {
        "total_enquiries": total_enquiries or 0,
        "new_leads": new_leads or 0,
        "pending_followups": pending_followups or 0,
        "open_escalations": open_escalations or 0,
        "total_conversations": total_conversations or 0,
    }


def course_wise_enquiries(db: Session, institute_id: int) -> list[dict]:
    """Enquiry counts grouped by course_interest (free-text), tenant-scoped."""
    rows = db.execute(
        select(
            func.coalesce(StudentLead.course_interest, "Unspecified").label("course"),
            func.count(StudentLead.id).label("count"),
        )
        .where(StudentLead.institute_id == institute_id)
        .group_by(StudentLead.course_interest)
        .order_by(func.count(StudentLead.id).desc())
    ).all()
    return [{"course": r.course, "count": r.count} for r in rows]
