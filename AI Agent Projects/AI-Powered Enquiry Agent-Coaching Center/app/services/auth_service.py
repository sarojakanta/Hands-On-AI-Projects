"""Admin authentication service."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import verify_password
from app.models import AdminUser


def authenticate_admin(db: Session, email: str, password: str) -> AdminUser | None:
    """Return the admin if credentials are valid and the account is active."""
    admin = db.scalar(select(AdminUser).where(AdminUser.email == email.lower().strip()))
    if admin is None or not admin.is_active:
        return None
    if not verify_password(password, admin.hashed_password):
        return None
    return admin


def get_admin_by_id(db: Session, admin_id: int) -> AdminUser | None:
    admin = db.get(AdminUser, admin_id)
    if admin is None or not admin.is_active:
        return None
    return admin
