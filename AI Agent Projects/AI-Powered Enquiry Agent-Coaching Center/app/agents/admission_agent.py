"""The admission enquiry agent.

Orchestrates one turn of conversation:

  1. Detect intent.
  2. Collect any student details present in the message into session context.
  3. Choose and run a tool (or RAG) based on intent.
  4. Ask the LLM to phrase the final reply, grounded strictly in the tool
     result / retrieved context. The LLM never sees the DB and cannot invent
     facts — the mock and real clients both answer only from what we pass.
  5. Persist messages (handled by the caller / conversation service).

Routing is deterministic for safety; wording is LLM-driven.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.intent import Intent, detect_intent
from app.agents.llm_client import LLMClient, get_llm_client
from app.agents.prompts import SYSTEM_PROMPT, extract_details
from app.config import get_settings
from app.config.logging_config import get_logger
from app.models import Conversation, Institute
from app.rag.sanitize import sanitize_text
from app.services import conversation_service, course_service
from app.tools.agent_tools import ToolContext, TOOL_REGISTRY

logger = get_logger(__name__)


@dataclass
class AgentReply:
    reply: str
    intent: str
    escalated: bool = False
    collected_lead: bool = False
    sources: list[dict] = field(default_factory=list)
    tool_used: str | None = None


class AdmissionAgent:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or get_llm_client()
        self._history: list[dict] = []

    def handle(
        self,
        db: Session,
        institute: Institute,
        conversation: Conversation,
        message: str,
    ) -> AgentReply:
        message = (message or "").strip()
        intent = detect_intent(message)
        ctx = ToolContext(db=db, institute_id=institute.id, conversation=conversation)

        # Merge any freshly-provided details into session context.
        session_ctx = conversation_service.get_context(db, conversation)
        found = extract_details(message)
        if found:
            session_ctx = conversation_service.update_context(db, conversation, found)

        # Remember the "course in focus" so later turns like "what are its
        # fees?" resolve against the previously-discussed course.
        mentioned_course = self._guess_course(ctx, message)
        if mentioned_course:
            session_ctx = conversation_service.update_context(
                db, conversation, {"focus_course": mentioned_course}
            )

        # Recent history (excludes the just-stored user message, which the
        # caller persists before invoking the agent) for LLM context.
        self._history = self._recent_history(db, conversation)

        # Pending-flow completion takes priority over intent routing. If the
        # student was asked for contact details (to escalate or register) and
        # has now supplied them, finish that flow regardless of how this
        # message is classified — e.g. "I am Ravi, phone 98..." may not match
        # the registration patterns but must still complete the escalation.
        have_contact = bool(session_ctx.get("name") and session_ctx.get("phone"))
        if have_contact and found:
            if session_ctx.get("pending_escalation"):
                reply = self._do_escalation(ctx, institute, session_ctx)
                reply.intent = Intent.COUNSELLING.value
                return reply
            if session_ctx.get("pending_registration"):
                reply = self._handle_registration(ctx, institute, message, session_ctx)
                reply.intent = Intent.ENQUIRY_REGISTRATION.value
                return reply

        # Route.
        handler = {
            Intent.GREETING: self._handle_greeting,
            Intent.COURSE_INFO: self._handle_course_info,
            Intent.FEE_DETAILS: self._handle_fees,
            Intent.BATCH_SCHEDULE: self._handle_batches,
            Intent.ELIGIBILITY: self._handle_eligibility,
            Intent.ADMISSION_PROCEDURE: self._handle_knowledge,
            Intent.COUNSELLING: self._handle_counselling,
            Intent.ENQUIRY_REGISTRATION: self._handle_registration,
            Intent.LEAD_STATUS: self._handle_lead_status,
            Intent.FOLLOWUP: self._handle_followup,
            Intent.KNOWLEDGE: self._handle_knowledge,
            Intent.OTHER: self._handle_knowledge,
        }.get(intent, self._handle_knowledge)

        reply = handler(ctx, institute, message, session_ctx)
        reply.intent = intent.value
        return reply

    # --- System prompt -------------------------------------------------
    def _system(self, institute: Institute) -> str:
        return SYSTEM_PROMPT.format(institute_name=institute.name)

    def _recent_history(
        self, db: Session, conversation: Conversation, limit: int | None = None
    ) -> list[dict]:
        """Return recent prior turns as chat messages for LLM context.

        ``limit`` defaults to ``settings.chat_history_turns`` (env-driven).
        The current user turn has already been persisted by the caller, so we
        drop the last user message to avoid duplicating it in _compose.
        """
        if limit is None:
            limit = get_settings().chat_history_turns
        msgs = conversation_service.get_history(db, conversation, limit=limit + 1)
        history = [
            {"role": m.role, "content": m.content}
            for m in msgs
            if m.role in ("user", "assistant")
        ]
        # Drop the trailing current user message if present.
        if history and history[-1]["role"] == "user":
            history = history[:-1]
        return history[-limit:]

    def _compose(
        self,
        institute: Institute,
        question: str,
        tool_result: str | None = None,
        grounding: str | None = None,
    ) -> str:
        """Build the chat (history + tagged current turn) and call the LLM."""
        parts = [f"[QUESTION]{sanitize_text(question)}[/QUESTION]"]
        if tool_result:
            parts.append(f"[TOOL_RESULT]{tool_result}[/TOOL_RESULT]")
        if grounding:
            parts.append(f"[GROUNDING]{grounding}[/GROUNDING]")
        messages = [*self._history, {"role": "user", "content": "\n".join(parts)}]
        return self._llm.complete(self._system(institute), messages)

    # --- Handlers ------------------------------------------------------
    def _handle_greeting(self, ctx, institute, message, session_ctx) -> AgentReply:
        reply = (
            f"Hi! Welcome to {institute.name}. I'm an AI assistant and I can "
            "help you with course details, fees, batch schedules, eligibility "
            "and admissions. You can also ask to speak with a human counsellor "
            "any time. What would you like to know?"
        )
        return AgentReply(reply=reply, intent=Intent.GREETING.value)

    def _run_course_tool(self, ctx, institute, message, tool_name, session_ctx) -> tuple[AgentReply | None, str | None]:
        """Shared logic for course-name tools. Returns (early_reply, tool_message)."""
        course_name = self._resolve_course(ctx, message, session_ctx)
        if not course_name:
            courses = course_service.list_courses(ctx.db, ctx.institute_id)
            names = ", ".join(c.name for c in courses)
            reply = AgentReply(
                reply=(
                    "Which course are you interested in? We currently offer: "
                    f"{names}."
                ),
                intent="",
            )
            return reply, None
        result = TOOL_REGISTRY[tool_name](ctx, course_name=course_name)
        return None, result.message if result.ok else result.message

    def _handle_course_info(self, ctx, institute, message, session_ctx) -> AgentReply:
        early, tool_msg = self._run_course_tool(ctx, institute, message, "get_course_details", session_ctx)
        if early:
            return early
        reply = self._compose(institute, message, tool_result=tool_msg)
        return AgentReply(reply=reply, intent="", tool_used="get_course_details")

    def _handle_fees(self, ctx, institute, message, session_ctx) -> AgentReply:
        early, tool_msg = self._run_course_tool(ctx, institute, message, "get_course_fees", session_ctx)
        if early:
            return early
        reply = self._compose(institute, message, tool_result=tool_msg)
        return AgentReply(reply=reply, intent="", tool_used="get_course_fees")

    def _handle_batches(self, ctx, institute, message, session_ctx) -> AgentReply:
        early, tool_msg = self._run_course_tool(ctx, institute, message, "get_batch_schedule", session_ctx)
        if early:
            return early
        reply = self._compose(institute, message, tool_result=tool_msg)
        return AgentReply(reply=reply, intent="", tool_used="get_batch_schedule")

    def _handle_eligibility(self, ctx, institute, message, session_ctx) -> AgentReply:
        course_name = self._resolve_course(ctx, message, session_ctx)
        if course_name:
            result = TOOL_REGISTRY["get_course_details"](ctx, course_name=course_name)
            if result.ok:
                reply = self._compose(institute, message, tool_result=result.message)
                return AgentReply(reply=reply, intent="", tool_used="get_course_details")
        # Fall back to knowledge base for general eligibility questions.
        return self._handle_knowledge(ctx, institute, message, session_ctx)

    def _handle_knowledge(self, ctx, institute, message, session_ctx) -> AgentReply:
        result = TOOL_REGISTRY["search_knowledge_base"](ctx, question=message)
        if result.ok:
            reply = self._compose(institute, message, grounding=result.message)
            sources = (result.data or {}).get("sources", [])
            return AgentReply(
                reply=reply, intent="", sources=sources, tool_used="search_knowledge_base"
            )
        # No grounding -> honest fallback (compose with nothing -> mock fallback).
        reply = self._compose(institute, message)
        return AgentReply(reply=reply, intent="", tool_used="search_knowledge_base")

    def _handle_counselling(self, ctx, institute, message, session_ctx) -> AgentReply:
        """Escalate to a human — but collect name + phone first so the
        counsellor can actually follow up."""
        name = session_ctx.get("name")
        phone = session_ctx.get("phone")

        if not name or not phone:
            # Remember the student wants a counsellor, then ask for contact
            # details. The next turn (which supplies them) completes it.
            conversation_service.update_context(
                ctx.db, ctx.conversation, {"pending_escalation": True}
            )
            missing = []
            if not name:
                missing.append("your name")
            if not phone:
                missing.append("your phone number")
            ask = " and ".join(missing)
            return AgentReply(
                reply=(
                    "I'll connect you with one of our human counsellors. So "
                    f"they can reach you, could you share {ask}? "
                    "(Email is optional.)"
                ),
                intent="",
            )

        return self._do_escalation(ctx, institute, session_ctx)

    def _do_escalation(self, ctx, institute, session_ctx) -> AgentReply:
        """Create/attach a lead with the student's contact details, then
        raise the escalation linked to that lead's conversation."""
        name = session_ctx.get("name")
        phone = session_ctx.get("phone")
        email = session_ctx.get("email")
        course = session_ctx.get("course") or session_ctx.get("focus_course")

        # Ensure a lead exists (so the counsellor has contact details). This
        # links the lead to the conversation; the escalation is reachable from
        # the conversation's lead_id in the dashboard.
        if not session_ctx.get("lead_id"):
            lead_res = TOOL_REGISTRY["create_student_lead"](
                ctx, name=name, phone=phone, email=email, course=course
            )
            session_ctx = conversation_service.get_context(ctx.db, ctx.conversation)
            lead_collected = lead_res.ok
        else:
            lead_collected = False

        reason = (
            f"Student {name} ({phone}) requested to speak with a human "
            "counsellor."
        )
        result = TOOL_REGISTRY["escalate_to_counsellor"](
            ctx, conversation_id=ctx.conversation.session_id, reason=reason
        )
        # Clear the pending flag.
        conversation_service.update_context(
            ctx.db, ctx.conversation, {"pending_escalation": False}
        )
        reply = (
            f"Thanks {name}! I've flagged this for one of our human "
            f"counsellors. They'll contact you on {phone}. Is there anything "
            "else I can help with in the meantime?"
        )
        return AgentReply(
            reply=reply,
            intent="",
            escalated=True,
            collected_lead=lead_collected,
            tool_used="escalate_to_counsellor",
        )

    def _handle_registration(self, ctx, institute, message, session_ctx) -> AgentReply:
        name = session_ctx.get("name")
        phone = session_ctx.get("phone")
        email = session_ctx.get("email")
        course = session_ctx.get("course") or self._guess_course(ctx, message)
        if course:
            conversation_service.update_context(ctx.db, ctx.conversation, {"course": course})

        missing = []
        if not name:
            missing.append("your name")
        if not phone:
            missing.append("your phone number")
        if missing:
            # Keep the prompt consistent with whichever flow is in progress.
            if session_ctx.get("pending_escalation"):
                ask = " and ".join(missing)
                return AgentReply(
                    reply=(
                        "To connect you with a counsellor, I just need "
                        f"{ask}. (Email is optional.)"
                    ),
                    intent="",
                )
            # Mark that a registration is awaiting details so the next turn
            # completes it even if its intent is classified differently.
            conversation_service.update_context(
                ctx.db, ctx.conversation, {"pending_registration": True}
            )
            ask = " and ".join(missing)
            return AgentReply(
                reply=(
                    f"I'd be happy to register your enquiry. Could you share "
                    f"{ask}? (Email is optional.)"
                ),
                intent="",
            )

        # If the student was waiting to be escalated, complete that now
        # (which also creates the lead with their contact details).
        if session_ctx.get("pending_escalation"):
            return self._do_escalation(ctx, institute, session_ctx)

        result = TOOL_REGISTRY["create_student_lead"](
            ctx, name=name, phone=phone, email=email, course=course
        )
        conversation_service.update_context(
            ctx.db, ctx.conversation, {"pending_registration": False}
        )
        return AgentReply(
            reply=result.message,
            intent="",
            collected_lead=result.ok,
            tool_used="create_student_lead",
        )

    def _handle_lead_status(self, ctx, institute, message, session_ctx) -> AgentReply:
        phone = session_ctx.get("phone")
        if not phone:
            return AgentReply(
                reply=(
                    "Sure — what's the phone number you used when you "
                    "registered the enquiry?"
                ),
                intent="",
            )
        result = TOOL_REGISTRY["get_lead_status"](ctx, phone=phone)
        return AgentReply(reply=result.message, intent="", tool_used="get_lead_status")

    def _handle_followup(self, ctx, institute, message, session_ctx) -> AgentReply:
        lead_id = session_ctx.get("lead_id")
        if not lead_id:
            # Need a lead first — nudge towards registration.
            return AgentReply(
                reply=(
                    "I can arrange a callback. First, could you share your name "
                    "and phone number so I can register your enquiry?"
                ),
                intent="",
            )
        result = TOOL_REGISTRY["create_followup_request"](
            ctx, lead_id=lead_id, notes="Requested via chat."
        )
        return AgentReply(reply=result.message, intent="", tool_used="create_followup_request")

    # --- Helpers -------------------------------------------------------
    def _guess_course(self, ctx, message: str) -> str | None:
        """Match a known course name mentioned in the message."""
        courses = course_service.list_courses(ctx.db, ctx.institute_id)
        text = (message or "").lower()
        # Longest name first so "full stack development" beats "development".
        for course in sorted(courses, key=lambda c: -len(c.name)):
            name = course.name.lower()
            if name in text:
                return course.name
            # Match on a distinctive first word (python, java, data...).
            first = name.split()[0]
            if len(first) >= 4 and first in text:
                return course.name
        return None

    def _resolve_course(self, ctx, message: str, session_ctx: dict) -> str | None:
        """Resolve the target course from the message, else the focus course.

        This is what lets a follow-up like "and its fees?" work after the
        student has already been talking about a specific course.
        """
        return self._guess_course(ctx, message) or session_ctx.get("focus_course")
