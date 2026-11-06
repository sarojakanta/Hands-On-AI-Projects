"""Shared FastAPI dependencies: DB session and tenant resolution."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Institute
from app.services import institute_service
from app.services.exceptions import NotFoundError


def get_tenant(
    db: Session = Depends(get_db),
    x_institute_key: str | None = Header(default=None, alias="X-Institute-Key"),
) -> Institute:
    """Resolve the current institute (tenant).

    Resolution order:
      1. If an ``X-Institute-Key`` header is supplied, resolve by API key.
      2. Otherwise, fall back to the single institute ONLY when exactly one
         is configured (convenience for a single-institute deployment).
      3. When multiple institutes exist and no key is given, require a key
         (400) so a request can never be routed to the wrong tenant.
    """
    try:
        if x_institute_key:
            return institute_service.get_by_api_key(db, x_institute_key)
        if institute_service.count_active(db) <= 1:
            return institute_service.get_default(db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Multiple institutes are configured. Provide an "
                "'X-Institute-Key' header to identify the institute."
            ),
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


__all__ = ["get_db", "get_tenant"]
