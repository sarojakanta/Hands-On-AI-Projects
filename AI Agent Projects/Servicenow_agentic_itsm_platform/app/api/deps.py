"""Dependency injection: shared services, authentication and role guards."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import ApiError
from app.approvals import ApprovalService
from app.config import settings
from app.db.base import session_scope
from app.db.repositories import UserRepository
from app.knowledge.retriever import KnowledgeRetriever
from app.security.auth import AuthError, decode_token
from app.security.rbac import Principal, has_role
from app.servicenow.client import ServiceNowClient
from app.services.investigation import InvestigationService

bearer = HTTPBearer(auto_error=False, description="JWT from POST /api/v1/auth/login")


@dataclass
class Services:
    servicenow: ServiceNowClient
    knowledge: KnowledgeRetriever
    investigations: InvestigationService
    approvals: ApprovalService


@lru_cache
def get_services() -> Services:
    """Process-wide singletons. Override with ``app.dependency_overrides`` or ``get_services.cache_clear()``."""
    servicenow = ServiceNowClient()
    knowledge = KnowledgeRetriever()
    approvals = ApprovalService(servicenow)
    return Services(servicenow, knowledge, InvestigationService(servicenow, knowledge, approvals), approvals)


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if not settings.auth_enabled:  # development only; refused at startup in production
        return Principal.of("anonymous", "admin")
    if creds is None:
        raise ApiError(401, "unauthenticated", "Authentication required", {"WWW-Authenticate": "Bearer"})
    try:
        principal = decode_token(creds.credentials)
    except AuthError as exc:
        raise ApiError(401, "unauthenticated", str(exc), {"WWW-Authenticate": "Bearer"}) from exc
    with session_scope() as s:  # deactivated or deleted users lose access immediately
        user = UserRepository(s).by_username(principal.username)
        if user is None or not user.is_active:
            raise ApiError(401, "unauthenticated", "Account is disabled or no longer exists",
                           {"WWW-Authenticate": "Bearer"})
        roles = frozenset(r for r in user.roles.split(",") if r)  # roles from the DB, not the (stale) token
    return Principal(principal.username, roles)


def require_role(role: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not has_role(principal, role):
            raise ApiError(403, "forbidden", f"The '{role}' role is required")
        return principal

    return dependency
