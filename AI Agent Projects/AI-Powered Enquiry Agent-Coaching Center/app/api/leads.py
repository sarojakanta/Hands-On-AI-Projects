"""Public lead and follow-up endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_tenant
from app.models import Institute
from app.schemas.lead import (
    FollowUpCreate,
    FollowUpOut,
    LeadCreate,
    LeadOut,
)
from app.services import lead_service
from app.services.exceptions import NotFoundError, ValidationError

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreate,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Register a prospective-student enquiry as a lead."""
    try:
        return lead_service.create_lead(db, tenant.id, payload)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/status", response_model=LeadOut)
def get_lead_status(
    phone: str,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Look up the latest lead for a phone number (enquiry status)."""
    lead = lead_service.get_lead_by_phone(db, tenant.id, phone)
    if lead is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No enquiry found for that phone number."
        )
    return lead


@router.post(
    "/followups", response_model=FollowUpOut, status_code=status.HTTP_201_CREATED
)
def create_followup(
    payload: FollowUpCreate,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Create a follow-up request for a lead."""
    try:
        return lead_service.create_followup(db, tenant.id, payload)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
