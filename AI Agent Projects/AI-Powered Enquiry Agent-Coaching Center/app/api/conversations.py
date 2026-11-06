"""Public conversation-history endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_tenant
from app.models import Institute
from app.schemas.conversation import ConversationOut
from app.services import conversation_service
from app.services.exceptions import NotFoundError

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("/{session_id}", response_model=ConversationOut)
def get_conversation(
    session_id: str,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Return a conversation and its message history (tenant-scoped)."""
    try:
        return conversation_service.get_conversation(db, tenant.id, session_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
