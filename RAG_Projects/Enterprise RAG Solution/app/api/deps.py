"""FastAPI dependencies. Handlers receive services through these functions so tests can override them."""

from __future__ import annotations

from fastapi import Request

from app.container import AppServices
from app.core.errors import AppError
from app.ingestion.service import IngestionService
from app.llm.service import LLMService
from app.rag.pipeline import RagPipeline


def get_services(request: Request) -> AppServices:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise AppError("The application is still starting up.", status_code=503, code="starting_up")
    return services


def get_llm_service(request: Request) -> LLMService:
    return get_services(request).llm


def get_rag_pipeline(request: Request) -> RagPipeline:
    return get_services(request).rag


def get_ingestion(request: Request) -> IngestionService:
    return get_services(request).ingestion
