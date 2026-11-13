from fastapi import APIRouter, Depends

from app.api.deps import current_principal, require_role
from app.api.errors import ApiError
from app.api.schemas import LoginRequest, TokenResponse, UserCreate, UserUpdate
from app.config import settings
from app.db.base import session_scope
from app.db.repositories import AuditRepository, UserRepository
from app.security import auth
from app.security.rbac import Principal

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
def login(body: LoginRequest):
    if not settings.auth_enabled:
        raise ApiError(400, "auth_disabled", "Authentication is disabled in this environment")
    principal = auth.authenticate(body.username, body.password)
    token, ttl = auth.create_access_token(principal.username, principal.roles)
    return TokenResponse(access_token=token, expires_in=ttl, username=principal.username,
                         roles=sorted(principal.roles))


@router.get("/auth/me", summary="Current identity")
def me(principal: Principal = Depends(current_principal)):
    return {"username": principal.username, "roles": sorted(principal.roles), "auth_enabled": settings.auth_enabled}


@router.get("/users", summary="List users (admin)")
def list_users(_: Principal = Depends(require_role("admin"))):
    with session_scope() as s:
        return [auth.user_dict(u) for u in UserRepository(s).list(limit=500)]


@router.post("/users", status_code=201, summary="Create a user (admin)")
def create_user(body: UserCreate, admin: Principal = Depends(require_role("admin"))):
    return auth.create_user(body.username, body.password, list(body.roles), body.email, actor=admin.username)


@router.patch("/users/{username}", summary="Update roles, status or password (admin)")
def update_user(username: str, body: UserUpdate, admin: Principal = Depends(require_role("admin"))):
    if username == admin.username and (body.is_active is False or (body.roles and "admin" not in body.roles)):
        raise ApiError(400, "self_lockout", "You can not deactivate or demote your own admin account")
    new_hash = auth.hash_password(body.password) if body.password else None
    with session_scope() as s:
        user = UserRepository(s).by_username(username)
        if user is None:
            raise KeyError("User not found")
        changes = {}
        if body.roles is not None:
            user.roles, changes["roles"] = ",".join(sorted(body.roles)), sorted(body.roles)
        if body.is_active is not None:
            user.is_active, changes["is_active"] = body.is_active, body.is_active
        if new_hash:
            user.password_hash, changes["password"] = new_hash, "changed"
        AuditRepository(s).record(admin.username, "user.updated", "user", username, **changes)
        return auth.user_dict(user)
