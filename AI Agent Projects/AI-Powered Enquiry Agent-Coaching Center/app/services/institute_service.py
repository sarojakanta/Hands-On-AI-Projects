"""Institute (tenant) lookup service."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Institute
from app.services.exceptions import NotFoundError


def get_by_api_key(db: Session, api_key: str) -> Institute:
    """Resolve the tenant from a public API key."""
    inst = db.scalar(
        select(Institute).where(
            Institute.api_key == api_key, Institute.is_active.is_(True)
        )
    )
    if inst is None:
        raise NotFoundError("Unknown or inactive institute API key.")
    return inst


def get_by_id(db: Session, institute_id: int) -> Institute:
    inst = db.get(Institute, institute_id)
    if inst is None or not inst.is_active:
        raise NotFoundError("Institute not found.")
    return inst


def get_by_slug(db: Session, slug: str) -> Institute:
    inst = db.scalar(select(Institute).where(Institute.slug == slug))
    if inst is None:
        raise NotFoundError("Institute not found.")
    return inst


def count_active(db: Session) -> int:
    """Return the number of active institutes."""
    from sqlalchemy import func

    return db.scalar(
        select(func.count(Institute.id)).where(Institute.is_active.is_(True))
    ) or 0


def get_default(db: Session) -> Institute:
    """Return the first active institute.

    Used as a convenience when exactly one institute is configured. With
    multiple institutes callers must resolve the tenant explicitly (API key),
    so ``get_tenant`` only falls back to this when a single institute exists.
    """
    inst = db.scalar(
        select(Institute).where(Institute.is_active.is_(True)).order_by(Institute.id)
    )
    if inst is None:
        raise NotFoundError("No institute configured. Run the seed script.")
    return inst


def list_active(db: Session) -> list[Institute]:
    """Return all active institutes (ordered by name)."""
    return list(
        db.scalars(
            select(Institute)
            .where(Institute.is_active.is_(True))
            .order_by(Institute.name)
        ).all()
    )
