"""
Endpoints used by the original single-page UI (``static/app.js``), now backed by the persistent stack.

The URLs and JSON shapes are unchanged on purpose so the existing front end keeps working: errors are
``{"detail": "..."}`` and documents are listed as ``{"filename", "chunks"}`` (with a few extra fields).
New integrations should use ``/api/v1/...``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_services
from app.container import AppServices
from app.core.config import PROJECT_ROOT
from app.core.errors import AppError
from app.ingestion.errors import DocumentTooLargeError
from app.ingestion.types import DocumentInfo

router = APIRouter(tags=["legacy-ui"])

INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


def _legacy_error(exc: AppError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _legacy_documents(documents: list[DocumentInfo]) -> list[dict[str, Any]]:
    return [
        {
            "filename": d.name,
            "chunks": d.chunk_count,
            "id": d.id,
            "version": d.active_version.version_number if d.active_version else None,
            "status": d.status,
            "stale": d.stale,
        }
        for d in documents
    ]


@router.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(str(INDEX_HTML))


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/api/query")
async def query_documents(req: QueryRequest, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Retrieval only (no LLM): the best passage as the "answer", plus every retrieved chunk."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    if not services.ingestion.ready:
        raise HTTPException(status_code=503, detail="RAG engine is still initializing. Try again in a moment.")
    try:
        chunks = await services.retriever.retrieve(req.query.strip(), req.top_k)
    except AppError as exc:
        raise _legacy_error(exc)

    if not chunks:
        return {"answer": "I couldn't find relevant information for your query.", "sources": [], "chunks": []}
    return {
        "answer": chunks[0].text,
        "sources": sorted({c.source for c in chunks}),
        "chunks": [{"chunk": c.text, "source": c.source, "score": c.score} for c in chunks],
        "query": req.query.strip(),
    }


@router.get("/api/documents")
async def list_documents(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    if not services.ingestion.ready:
        return {"documents": [], "status": "indexing"}
    documents = await anyio.to_thread.run_sync(services.ingestion.list_documents)
    return {"documents": _legacy_documents(documents), "status": "ready"}


@router.get("/api/status")
async def status(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    ready = services.ingestion.ready
    documents = await anyio.to_thread.run_sync(services.ingestion.list_documents) if ready else []
    return {
        "ready": ready,
        "total_chunks": sum(d.chunk_count for d in documents),
        "documents": len([d for d in documents if d.active_version]),
    }


@router.post("/api/upload")
async def upload(file: UploadFile = File(...), services: AppServices = Depends(get_services)) -> dict[str, Any]:
    limit = services.settings.max_upload_bytes
    try:
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise DocumentTooLargeError(f"File is too large. Maximum size is {limit // (1024 * 1024)} MB.")
        result = await anyio.to_thread.run_sync(services.ingestion.ingest, file.filename or "", data)
    except AppError as exc:
        raise _legacy_error(exc)

    documents = await anyio.to_thread.run_sync(services.ingestion.list_documents)
    name = result.document.name
    messages = {
        "created": f"'{name}' uploaded and indexed successfully.",
        "updated": f"'{name}' updated to version {result.version.version_number} and re-indexed.",
        "reindexed": f"'{name}' re-indexed.",
        "unchanged": f"'{name}' is already up to date (identical content).",
    }
    return {
        "message": messages[result.outcome],
        "documents": _legacy_documents(documents),
        "total_chunks": sum(d.chunk_count for d in documents),
    }


@router.delete("/api/documents/{filename}")
async def delete_document(filename: str, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Delete by file name. The name is looked up among stored documents; it is never used as a file path."""
    try:
        await anyio.to_thread.run_sync(services.ingestion.delete_by_name, filename)
    except AppError as exc:
        raise _legacy_error(exc)
    documents = await anyio.to_thread.run_sync(services.ingestion.list_documents)
    return {"message": f"'{filename}' deleted.", "documents": _legacy_documents(documents)}
