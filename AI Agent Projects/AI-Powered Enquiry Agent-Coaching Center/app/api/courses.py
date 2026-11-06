"""Public course information endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_tenant
from app.models import Institute
from app.schemas.course import BatchOut, CourseOut
from app.services import course_service
from app.services.exceptions import NotFoundError

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.get("", response_model=list[CourseOut])
def list_courses(
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """List all active courses for the current institute."""
    return course_service.list_courses(db, tenant.id, active_only=True)


@router.get("/{course_id}", response_model=CourseOut)
def get_course(
    course_id: int,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Get a single course by id (tenant-scoped)."""
    try:
        return course_service.get_course(db, tenant.id, course_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{course_id}/batches", response_model=list[BatchOut])
def get_batches(
    course_id: int,
    tenant: Institute = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """List active batches for a course."""
    try:
        return course_service.get_batches(db, tenant.id, course_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
