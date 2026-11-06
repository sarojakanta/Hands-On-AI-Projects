"""Course and batch service. Every query is scoped by institute_id."""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Batch, Course
from app.schemas.course import CourseCreate, CourseUpdate
from app.services.exceptions import NotFoundError


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "course"


def list_courses(db: Session, institute_id: int, active_only: bool = True) -> list[Course]:
    stmt = (
        select(Course)
        .where(Course.institute_id == institute_id)
        .options(selectinload(Course.batches))
        .order_by(Course.name)
    )
    if active_only:
        stmt = stmt.where(Course.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_course(db: Session, institute_id: int, course_id: int) -> Course:
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id, Course.institute_id == institute_id)
        .options(selectinload(Course.batches))
    )
    if course is None:
        raise NotFoundError(f"Course {course_id} not found for this institute.")
    return course


def find_course_by_name(db: Session, institute_id: int, name: str) -> Course | None:
    """Case-insensitive, fuzzy-ish lookup used by agent tools.

    Tries exact (case-insensitive) match first, then a contains match in
    either direction so "python" matches "Python Programming".
    """
    name = (name or "").strip()
    if not name:
        return None

    exact = db.scalar(
        select(Course)
        .where(
            Course.institute_id == institute_id,
            func.lower(Course.name) == name.lower(),
        )
        .options(selectinload(Course.batches))
    )
    if exact:
        return exact

    like = f"%{name.lower()}%"
    candidates = db.scalars(
        select(Course)
        .where(Course.institute_id == institute_id, Course.is_active.is_(True))
        .options(selectinload(Course.batches))
    ).all()
    for c in candidates:
        cn = c.name.lower()
        if name.lower() in cn or cn in like.strip("%"):
            return c
    return None


def get_batches(db: Session, institute_id: int, course_id: int) -> list[Batch]:
    # Ensure the course belongs to this tenant before returning its batches.
    get_course(db, institute_id, course_id)
    return list(
        db.scalars(
            select(Batch)
            .where(Batch.course_id == course_id, Batch.is_active.is_(True))
            .order_by(Batch.start_date)
        ).all()
    )


def create_course(db: Session, institute_id: int, payload: CourseCreate) -> Course:
    course = Course(
        institute_id=institute_id,
        name=payload.name,
        slug=_slugify(payload.name),
        description=payload.description,
        fee=payload.fee,
        currency=payload.currency,
        duration=payload.duration,
        eligibility=payload.eligibility,
        mode=payload.mode,
        is_active=True,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def update_course(
    db: Session, institute_id: int, course_id: int, payload: CourseUpdate
) -> Course:
    course = get_course(db, institute_id, course_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(course, field, value)
    if "name" in data and data["name"]:
        course.slug = _slugify(data["name"])
    db.commit()
    db.refresh(course)
    return course


def delete_course(db: Session, institute_id: int, course_id: int) -> None:
    course = get_course(db, institute_id, course_id)
    db.delete(course)
    db.commit()
