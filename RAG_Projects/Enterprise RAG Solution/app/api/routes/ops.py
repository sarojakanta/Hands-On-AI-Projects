"""Operational endpoints: liveness, readiness and Prometheus metrics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger("app.ops")

router = APIRouter(tags=["ops"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """The process is up and serving requests."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> Response:
    """200 when the knowledge base is usable, 503 while it is starting or if start-up failed."""
    services = getattr(request.app.state, "services", None)
    is_ready = bool(services is not None and services.ingestion.ready)
    return JSONResponse({"status": "ready" if is_ready else "starting"}, status_code=200 if is_ready else 503)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus text format. Disabled with ``METRICS_ENABLED=false``."""
    settings = request.app.state.settings
    if not settings.metrics_enabled:
        return JSONResponse({"detail": "Metrics are disabled."}, status_code=404)
    registry = request.app.state.metrics
    services = getattr(request.app.state, "services", None)
    registry.set_gauge("rag_ready", "1 when the knowledge base is ready.", 1.0 if services and services.ingestion.ready else 0.0)
    if services is not None and services.ingestion.ready:
        try:
            report = services.ingestion.index_report()
            registry.set_gauge("rag_documents", "Documents in the knowledge base.", report.documents)
            registry.set_gauge("rag_indexed_vectors", "Searchable vectors.", report.indexed_vectors)
            registry.set_gauge("rag_orphan_vectors", "Vectors that belong to no active version.", report.orphan_vectors)
            registry.set_gauge("rag_index_consistent", "1 when database and vector index agree.", 1.0 if report.consistent else 0.0)
        except Exception:   # noqa: BLE001 - metrics must never fail the scrape
            logger.warning("metrics_index_report_failed")
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
