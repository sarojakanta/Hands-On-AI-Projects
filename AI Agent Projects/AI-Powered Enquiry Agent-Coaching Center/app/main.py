"""BizAssist FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
import os

from fastapi.staticfiles import StaticFiles

from app.api import admin, auth, chat, conversations, courses, leads, pages
from app.config import get_settings
from app.config.logging_config import get_logger
from app.database.init_db import create_all
from app.schemas.common import HealthResponse
from app.services.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ValidationError,
)

logger = get_logger("app")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure tables exist at startup."""
    logger.info("Starting %s v%s (env=%s)", settings.app_name, __version__, settings.app_env)
    create_all()
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="AI-powered admission enquiry agent for coaching centres.",
        lifespan=lifespan,
    )

    # CORS — the widget may be embedded on institute websites. Origins are
    # configurable via CORS_ALLOW_ORIGINS (defaults to "*" for local/demo).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static files (dashboard + widget assets).
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    _register_exception_handlers(app)
    _register_routers(app)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            app=settings.app_name,
            version=__version__,
            llm_enabled=settings.llm_enabled,
        )

    return app


def _register_routers(app: FastAPI) -> None:
    app.include_router(courses.router)
    app.include_router(leads.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(pages.router)


def _register_exception_handlers(app: FastAPI) -> None:
    """Map service-layer exceptions to clean HTTP responses."""

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)}
        )

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)}
        )

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
        )

    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError):
        logger.exception("Unhandled service error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal error."},
        )


app = create_app()
