"""Auth dependencies for admin-protected routes.

Supports two token transports:
  * ``Authorization: Bearer <jwt>`` header  — for API clients / tests.
  * ``access_token`` cookie                 — for the browser dashboard.

Both resolve to the current :class:`AdminUser`. The admin's institute is the
tenant for every admin query, so tenant isolation is automatic.
"""
from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.database.session import get_db
from app.models import AdminUser
from app.services import auth_service

COOKIE_NAME = "access_token"


def _extract_token(authorization: str | None, cookie_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return cookie_token


def _resolve_admin(db: Session, token: str | None) -> AdminUser | None:
    if not token:
        return None
    claims = decode_access_token(token)
    if not claims:
        return None
    sub = claims.get("sub")
    if sub is None:
        return None
    try:
        admin_id = int(sub)
    except (TypeError, ValueError):
        return None
    return auth_service.get_admin_by_id(db, admin_id)


def get_current_admin(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
) -> AdminUser:
    """Require a valid admin (401 otherwise). For JSON API routes."""
    token = _extract_token(authorization, access_token)
    admin = _resolve_admin(db, token)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return admin


def get_current_admin_optional(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
) -> AdminUser | None:
    """Return the admin if authenticated, else None. For HTML page routes
    that redirect to login instead of returning 401."""
    token = _extract_token(authorization, access_token)
    return _resolve_admin(db, token)
