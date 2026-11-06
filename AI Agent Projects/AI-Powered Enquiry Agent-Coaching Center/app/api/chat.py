"""Public chat endpoint — the widget talks to this."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.admission_agent import AdmissionAgent
from app.api.deps import get_db, get_tenant
from app.auth.rate_limit import rate_limit_public_chat
from app.models import Institute
from app.schemas.conversation import ChatRequest, ChatResponse, ChatSource
from app.services import conversation_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "", response_model=ChatResponse, dependencies=[Depends(rate_limit_public_chat)]
)
def chat(
    payload: ChatRequest,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Handle one chat turn: run the agent, persist messages, return the reply.

    Rate-limited per client IP (see PUBLIC_CHAT_RATE_LIMIT).
    """
    conversation = conversation_service.get_or_create_conversation(
        db, tenant.id, payload.session_id
    )

    # Persist the user's message.
    conversation_service.add_message(db, conversation, "user", payload.message)

    # Run the agent.
    agent = AdmissionAgent()
    result = agent.handle(db, tenant, conversation, payload.message)

    # Persist the assistant reply with diagnostic metadata.
    conversation_service.add_message(
        db,
        conversation,
        "assistant",
        result.reply,
        meta={
            "intent": result.intent,
            "tool": result.tool_used,
            "escalated": result.escalated,
        },
    )

    return ChatResponse(
        session_id=conversation.session_id,
        reply=result.reply,
        intent=result.intent,
        escalated=result.escalated,
        collected_lead=result.collected_lead,
        sources=[ChatSource(**s) for s in result.sources],
    )
