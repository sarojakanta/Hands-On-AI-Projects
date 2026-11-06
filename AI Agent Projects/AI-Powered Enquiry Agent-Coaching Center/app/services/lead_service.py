"""Lead and follow-up service. All queries scoped by institute_id."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Course, FollowUp, StudentLead
from app.schemas.lead import FollowUpCreate, LeadCreate, LeadUpdate
from app.services.exceptions import NotFoundError, ValidationError

VALID_LEAD_STATUSES = {"new", "contacted", "enrolled", "closed"}
VALID_FOLLOWUP_STATUSES = {"pending", "scheduled", "done", "cancelled"}


def create_lead(db: Session, institute_id: int, payload: LeadCreate) -> StudentLead:
    """Register a prospective student enquiry as a lead.

    If a course_id is given it must belong to the same institute. If only a
    course_interest name is given we try to resolve it to a course.
    """
    course_id = payload.course_id
    if course_id is not None:
        course = db.scalar(
            select(Course).where(
                Course.id == course_id, Course.institute_id == institute_id
            )
        )
        if course is None:
            raise ValidationError("Selected course does not belong to this institute.")

    lead = StudentLead(
        institute_id=institute_id,
        course_id=course_id,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        course_interest=payload.course_interest,
        status="new",
        source=payload.source,
        notes=payload.notes,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def list_leads(
    db: Session, institute_id: int, status: str | None = None, limit: int = 100
) -> list[StudentLead]:
    stmt = (
        select(StudentLead)
        .where(StudentLead.institute_id == institute_id)
        .order_by(StudentLead.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(StudentLead.status == status)
    return list(db.scalars(stmt).all())


def get_lead(db: Session, institute_id: int, lead_id: int) -> StudentLead:
    lead = db.scalar(
        select(StudentLead).where(
            StudentLead.id == lead_id, StudentLead.institute_id == institute_id
        )
    )
    if lead is None:
        raise NotFoundError(f"Lead {lead_id} not found for this institute.")
    return lead


def get_lead_by_phone(
    db: Session, institute_id: int, phone: str
) -> StudentLead | None:
    """Return the most recent lead for a phone number (agent tool)."""
    return db.scalar(
        select(StudentLead)
        .where(
            StudentLead.institute_id == institute_id,
            StudentLead.phone == phone.strip(),
        )
        .order_by(StudentLead.created_at.desc())
    )


def update_lead(
    db: Session, institute_id: int, lead_id: int, payload: LeadUpdate
) -> StudentLead:
    lead = get_lead(db, institute_id, lead_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in VALID_LEAD_STATUSES:
        raise ValidationError(
            f"Invalid status. Allowed: {sorted(VALID_LEAD_STATUSES)}"
        )
    if "course_id" in data and data["course_id"] is not None:
        course = db.scalar(
            select(Course).where(
                Course.id == data["course_id"],
                Course.institute_id == institute_id,
            )
        )
        if course is None:
            raise ValidationError("Course does not belong to this institute.")
    for field, value in data.items():
        setattr(lead, field, value)
    db.commit()
    db.refresh(lead)
    return lead


def create_followup(
    db: Session, institute_id: int, payload: FollowUpCreate
) -> FollowUp:
    # Ensure the lead belongs to this tenant.
    lead = get_lead(db, institute_id, payload.lead_id)
    followup = FollowUp(
        institute_id=institute_id,
        lead_id=lead.id,
        preferred_time=payload.preferred_time,
        notes=payload.notes,
        status="pending",
    )
    db.add(followup)
    db.commit()
    db.refresh(followup)
    return followup


def list_followups(
    db: Session, institute_id: int, status: str | None = None
) -> list[FollowUp]:
    stmt = (
        select(FollowUp)
        .where(FollowUp.institute_id == institute_id)
        .order_by(FollowUp.created_at.desc())
    )
    if status:
        stmt = stmt.where(FollowUp.status == status)
    return list(db.scalars(stmt).all())


def set_followup_status(
    db: Session, institute_id: int, followup_id: int, status: str
) -> FollowUp:
    if status not in VALID_FOLLOWUP_STATUSES:
        raise ValidationError(
            f"Invalid status. Allowed: {sorted(VALID_FOLLOWUP_STATUSES)}"
        )
    followup = db.scalar(
        select(FollowUp).where(
            FollowUp.id == followup_id, FollowUp.institute_id == institute_id
        )
    )
    if followup is None:
        raise NotFoundError(f"Follow-up {followup_id} not found for this institute.")
    followup.status = status
    db.commit()
    db.refresh(followup)
    return followup
