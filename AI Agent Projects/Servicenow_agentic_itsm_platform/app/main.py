"""FastAPI application factory.

* Versioned API under ``/api/v1`` (JWT auth + RBAC); ``/health`` and ``/ready`` are public.
* Structured logging with request IDs, centralized exception handling, security headers.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import errors
from app.api.deps import get_services
from app.api.observability import RequestContextMiddleware, configure_logging
from app.api.v1 import agents, approvals, investigations, knowledge, llm, monitoring, records
from app.api.v1 import auth as auth_routes
from app.config import settings
from app.db.base import get_engine, init_db, session_scope
from app.security.auth import ensure_bootstrap_admin, validate_security_settings
from app.services.agent_configs import seed_agent_configs

VERSION = "1.0.0"
logger = logging.getLogger(__name__)

TAGS = [
    {"name": "auth", "description": "Login and user administration"},
    {"name": "records", "description": "ServiceNow incidents, problems, changes and service requests"},
    {"name": "investigations", "description": "Multi-agent workflow runs, timelines and reports"},
    {"name": "approvals", "description": "Human-in-the-loop approval requests"},
    {"name": "knowledge", "description": "Knowledge base documents and semantic search"},
    {"name": "agents", "description": "Agent configuration and statistics"},
    {"name": "llm", "description": "LLM providers and usage"},
    {"name": "monitoring", "description": "Dashboard metrics, audit log, ServiceNow call log, system status"},
    {"name": "health", "description": "Liveness and readiness"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    for warning in validate_security_settings():  # raises in production when unsafe
        logger.warning(warning)
    get_engine()
    if settings.auto_create_tables:
        init_db()
    seed_agent_configs()
    ensure_bootstrap_admin()
    interrupted = get_services().investigations.recover_interrupted_runs()
    if interrupted:
        logger.warning("Marked %d interrupted workflow run(s) as failed (resumable)", interrupted)
    logger.info("Started %s (%s), ServiceNow mode=%s", app.title, settings.app_env, settings.servicenow_mode)
    yield


health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Liveness probe")
def health():
    return {"status": "ok", "service": "enterprise-agentic-itsm", "version": VERSION}


@health_router.get("/ready", summary="Readiness probe (database reachable)")
def ready():
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unreachable"})
    return {"status": "ready"}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Enterprise Agentic ITSM Platform", version=VERSION, lifespan=lifespan, openapi_tags=TAGS,
        description="Multi-agent IT Service Management with ServiceNow, RAG and human-in-the-loop approvals. "
                    "Authenticate with `POST /api/v1/auth/login`, then send `Authorization: Bearer <token>`.")
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    errors.install(app)
    app.include_router(health_router)
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health_router)
    for module in (auth_routes, records, investigations, approvals, knowledge, agents, llm, monitoring):
        v1.include_router(module.router)
    app.include_router(v1)
    return app


app = create_app()
