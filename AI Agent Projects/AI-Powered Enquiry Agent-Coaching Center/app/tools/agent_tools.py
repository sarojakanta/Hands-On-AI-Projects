"""Agent tools.

Each tool is a plain callable taking a ``ToolContext`` (db session +
institute_id + conversation) and validated arguments, returning a
``ToolResult``. The agent invokes these; the LLM never touches the DB
directly. All reads/writes are tenant-scoped via institute_id.

Implements the 8 tools from the spec:
  1. get_course_details(course_name)
  2. get_course_fees(course_name)
  3. get_batch_schedule(course_name)
  4. search_knowledge_base(question)
  5. create_student_lead(name, phone, email, course)
  6. get_lead_status(phone)
  7. create_followup_request(lead_id, preferred_time, notes)
  8. escalate_to_counsellor(conversation_id, reason)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.config.logging_config import get_logger
from app.models import Conversation
from app.rag.retriever import build_context, retrieve
from app.schemas.lead import FollowUpCreate, LeadCreate
from app.services import conversation_service, course_service, lead_service
from app.services.exceptions import NotFoundError, ServiceError, ValidationError
from app.tools.schemas import (
    CourseNameInput,
    CreateLeadInput,
    EscalateInput,
    FollowUpInput,
    LeadStatusInput,
    SearchInput,
    ToolResult,
)

logger = get_logger(__name__)


@dataclass
class ToolContext:
    """Runtime context injected into every tool call (not LLM-controlled)."""

    db: Session
    institute_id: int
    conversation: Conversation


def _bad_args(exc: PydanticValidationError) -> ToolResult:
    msgs = "; ".join(e["msg"] for e in exc.errors())
    return ToolResult(ok=False, message=f"Invalid arguments: {msgs}")


def _money(amount: float, currency: str) -> str:
    symbol = "₹" if currency == "INR" else ""
    return f"{symbol}{amount:,.0f} {currency}".strip()


# --- 1. get_course_details ------------------------------------------------
def get_course_details(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = CourseNameInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    course = course_service.find_course_by_name(ctx.db, ctx.institute_id, args.course_name)
    if not course:
        return ToolResult(
            ok=False,
            message=f"I couldn't find a course matching '{args.course_name}'.",
        )
    parts = [f"{course.name}"]
    if course.description:
        parts.append(course.description)
    details = []
    if course.duration:
        details.append(f"Duration: {course.duration}")
    details.append(f"Fee: {_money(course.fee, course.currency)}")
    if course.mode:
        details.append(f"Mode: {course.mode}")
    if course.eligibility:
        details.append(f"Eligibility: {course.eligibility}")
    message = parts[0] + ("\n" + parts[1] if len(parts) > 1 else "") + "\n" + "\n".join(details)
    return ToolResult(
        ok=True,
        message=message,
        data={
            "id": course.id,
            "name": course.name,
            "fee": course.fee,
            "currency": course.currency,
            "duration": course.duration,
            "mode": course.mode,
            "eligibility": course.eligibility,
        },
    )


# --- 2. get_course_fees ---------------------------------------------------
def get_course_fees(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = CourseNameInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    course = course_service.find_course_by_name(ctx.db, ctx.institute_id, args.course_name)
    if not course:
        return ToolResult(
            ok=False, message=f"I couldn't find a course matching '{args.course_name}'."
        )
    msg = f"The fee for {course.name} is {_money(course.fee, course.currency)}"
    if course.duration:
        msg += f" for a duration of {course.duration}"
    msg += "."
    return ToolResult(
        ok=True,
        message=msg,
        data={"name": course.name, "fee": course.fee, "currency": course.currency},
    )


# --- 3. get_batch_schedule ------------------------------------------------
def get_batch_schedule(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = CourseNameInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    course = course_service.find_course_by_name(ctx.db, ctx.institute_id, args.course_name)
    if not course:
        return ToolResult(
            ok=False, message=f"I couldn't find a course matching '{args.course_name}'."
        )
    batches = course_service.get_batches(ctx.db, ctx.institute_id, course.id)
    if not batches:
        return ToolResult(
            ok=True,
            message=(
                f"There are no scheduled batches listed for {course.name} right "
                "now. A counsellor can tell you about upcoming batches."
            ),
            data={"course": course.name, "batches": []},
        )
    lines = [f"Upcoming batches for {course.name}:"]
    batch_data = []
    for b in batches:
        start = b.start_date.strftime("%d %b %Y") if b.start_date else "TBA"
        seat_txt = (
            f"{b.seats_available} seats left"
            if b.seats_available > 0
            else "currently full"
        )
        lines.append(f"- {b.name}: {b.timing or 'timing TBA'}, starts {start} ({seat_txt})")
        batch_data.append(
            {
                "name": b.name,
                "timing": b.timing,
                "start_date": start,
                "seats_available": b.seats_available,
            }
        )
    return ToolResult(
        ok=True, message="\n".join(lines), data={"course": course.name, "batches": batch_data}
    )


# --- 4. search_knowledge_base ---------------------------------------------
def search_knowledge_base(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = SearchInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    chunks = retrieve(ctx.institute_id, args.question)
    if not chunks:
        return ToolResult(
            ok=False,
            message="I couldn't find that in our documents.",
            data={"sources": []},
        )
    context = build_context(chunks)
    sources = [{"title": c.title, "snippet": c.text[:160]} for c in chunks]
    return ToolResult(ok=True, message=context, data={"sources": sources})


# --- 5. create_student_lead -----------------------------------------------
def create_student_lead(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = CreateLeadInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)

    # Resolve the course name to an id if possible (best-effort).
    course_id = None
    if args.course:
        course = course_service.find_course_by_name(ctx.db, ctx.institute_id, args.course)
        course_id = course.id if course else None

    try:
        payload = LeadCreate(
            name=args.name,
            phone=args.phone,
            email=args.email,
            course_interest=args.course,
            course_id=course_id,
            source="chat_widget",
        )
    except PydanticValidationError as exc:
        return _bad_args(exc)

    try:
        lead = lead_service.create_lead(ctx.db, ctx.institute_id, payload)
    except (ValidationError, ServiceError) as exc:
        return ToolResult(ok=False, message=str(exc))

    conversation_service.link_lead(ctx.db, ctx.conversation, lead.id)
    conversation_service.update_context(
        ctx.db,
        ctx.conversation,
        {"lead_id": lead.id, "name": lead.name, "phone": lead.phone},
    )
    return ToolResult(
        ok=True,
        message=(
            f"Thanks {lead.name}! I've registered your enquiry"
            + (f" for {args.course}" if args.course else "")
            + ". One of our counsellors will reach out to you shortly on "
            f"{lead.phone}."
        ),
        data={"lead_id": lead.id, "name": lead.name, "phone": lead.phone},
    )


# --- 6. get_lead_status ---------------------------------------------------
def get_lead_status(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = LeadStatusInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    lead = lead_service.get_lead_by_phone(ctx.db, ctx.institute_id, args.phone)
    if not lead:
        return ToolResult(
            ok=False,
            message=f"I couldn't find an enquiry registered for {args.phone}.",
        )
    return ToolResult(
        ok=True,
        message=(
            f"Your enquiry (for {lead.course_interest or 'a course'}) is "
            f"currently '{lead.status}'. A counsellor will be in touch."
        ),
        data={"lead_id": lead.id, "status": lead.status},
    )


# --- 7. create_followup_request -------------------------------------------
def create_followup_request(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = FollowUpInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)

    preferred = None
    if args.preferred_time:
        preferred = _try_parse_dt(args.preferred_time)

    try:
        payload = FollowUpCreate(
            lead_id=args.lead_id, preferred_time=preferred, notes=args.notes
        )
        followup = lead_service.create_followup(ctx.db, ctx.institute_id, payload)
    except NotFoundError as exc:
        return ToolResult(ok=False, message=str(exc))
    except ServiceError as exc:
        return ToolResult(ok=False, message=str(exc))

    return ToolResult(
        ok=True,
        message=(
            "I've scheduled a follow-up request. A counsellor will contact you"
            + (f" around {args.preferred_time}" if args.preferred_time else " soon")
            + "."
        ),
        data={"followup_id": followup.id},
    )


# --- 8. escalate_to_counsellor --------------------------------------------
def escalate_to_counsellor(ctx: ToolContext, **kwargs) -> ToolResult:
    try:
        args = EscalateInput(**kwargs)
    except PydanticValidationError as exc:
        return _bad_args(exc)
    esc = conversation_service.escalate(
        ctx.db, ctx.institute_id, ctx.conversation, args.reason
    )
    return ToolResult(
        ok=True,
        message=(
            "I've flagged this for one of our human counsellors. They'll "
            "follow up with you. Is there anything else I can help with in the "
            "meantime?"
        ),
        data={"escalation_id": esc.id},
    )


def _try_parse_dt(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


# Registry so the agent can look tools up by name.
TOOL_REGISTRY = {
    "get_course_details": get_course_details,
    "get_course_fees": get_course_fees,
    "get_batch_schedule": get_batch_schedule,
    "search_knowledge_base": search_knowledge_base,
    "create_student_lead": create_student_lead,
    "get_lead_status": get_lead_status,
    "create_followup_request": create_followup_request,
    "escalate_to_counsellor": escalate_to_counsellor,
}
