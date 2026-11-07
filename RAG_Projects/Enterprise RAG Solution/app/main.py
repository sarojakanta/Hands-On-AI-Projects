"""
FastAPI application factory.

``create_app`` wires routers, error handling and the static UI. Services are built in the lifespan so that a
failure to start the knowledge base (for example the embedding model cannot be loaded) leaves the server
running and reporting ``ready: false`` instead of crashing.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio.to_thread
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes.chat import router as chat_router
from app.api.routes.documents import router as documents_router
from app.api.routes.legacy import router as legacy_router
from app.api.routes.llm import router as llm_router
from app.api.routes.ops import router as ops_router
from app.container import AppServices, build_services
from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.observability import ObservabilityMiddleware, new_metrics
from app.core.ratelimit import RateLimitMiddleware, SlidingWindowLimiter

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None, services: AppServices | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = services or build_services(settings)
        app.state.services = container
        try:
            await anyio.to_thread.run_sync(container.start)
            logger.info("startup_complete")
        except Exception:   # noqa: BLE001 - keep serving: /api/status reports ready=false
            logger.exception("startup_failed (knowledge base is not available)")
        yield
        await container.aclose()
        logger.info("shutdown_complete")

    app = FastAPI(title="Saroj RAG Demo", lifespan=lifespan)
    register_error_handlers(app)

    metrics = new_metrics()
    app.state.settings = settings
    app.state.metrics = metrics
    # Added innermost first: observability must be outermost so it also sees (and counts) rejected requests.
    if settings.rate_limit_enabled:
        limiter = SlidingWindowLimiter(
            {"llm": settings.rate_limit_llm_per_minute, "write": settings.rate_limit_write_per_minute}
        )
        app.state.rate_limiter = limiter
        app.add_middleware(RateLimitMiddleware, limiter=limiter, metrics=metrics, trust_proxy=settings.trust_proxy_headers)
    app.add_middleware(ObservabilityMiddleware, metrics=metrics, trust_proxy=settings.trust_proxy_headers)

    # Routers first; the static mount must be last because it matches everything under /static.
    app.include_router(ops_router)         # /health/live, /health/ready, /metrics
    app.include_router(llm_router)         # /api/v1/llm/*
    app.include_router(chat_router)        # /api/v1/chat, /api/v1/conversations/*
    app.include_router(documents_router)   # /api/v1/documents*, /api/v1/index/status
    app.include_router(legacy_router)      # /, /api/query, /api/upload, /api/documents, /api/status

    static_dir: Path = PROJECT_ROOT / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app
