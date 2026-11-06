"""Admin authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import COOKIE_NAME, get_current_admin
from app.auth.security import create_access_token
from app.config import get_settings
from app.database.session import get_db
from app.models import AdminUser
from app.schemas.auth import AdminOut, LoginRequest, TokenResponse
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Authenticate an admin and issue a JWT (also set as an HTTP-only cookie)."""
    admin = auth_service.authenticate_admin(db, payload.email, payload.password)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    token = create_access_token(
        subject=admin.id,
        extra_claims={"institute_id": admin.institute_id, "role": admin.role},
    )
    # HTTP-only cookie for the browser dashboard. Secure flag on in prod.
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(COOKIE_NAME)
    return {"detail": "Logged out."}


@router.get("/me", response_model=AdminOut)
def me(admin: AdminUser = Depends(get_current_admin)):
    """Return the current authenticated admin."""
    return AdminOut(
        id=admin.id,
        email=admin.email,
        full_name=admin.full_name,
        role=admin.role,
        institute_id=admin.institute_id,
    )
