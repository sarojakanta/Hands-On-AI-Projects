"""Document management: upload (versioned), inventory, versions, delete, re-index and index status."""

from __future__ import annotations

from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Depends, File, Response, UploadFile

from app.api.deps import get_services
from app.api.schemas.documents import (
    document_to_dict,
    index_report_to_dict,
    ingest_result_to_dict,
    reindex_summary_to_dict,
    version_to_dict,
)
from app.container import AppServices
from app.ingestion.errors import DocumentTooLargeError

router = APIRouter(prefix="/api/v1", tags=["documents"])


@router.post("/documents")
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    services: AppServices = Depends(get_services),
) -> dict[str, Any]:
    """
    Upload a document. A new name creates the document (201); an existing name with changed content creates
    a new version that replaces the old one for search (201); identical content changes nothing (200).
    Identical content under a different name is rejected with 409.
    """
    limit = services.settings.max_upload_bytes
    data = await file.read(limit + 1)   # one byte over the limit is enough to know it is too large
    if len(data) > limit:
        raise DocumentTooLargeError(f"File is too large. Maximum size is {limit // (1024 * 1024)} MB.")

    result = await anyio.to_thread.run_sync(services.ingestion.ingest, file.filename or "", data)
    response.status_code = 200 if result.outcome == "unchanged" else 201
    return ingest_result_to_dict(result)


@router.get("/documents")
async def list_documents(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    documents = await anyio.to_thread.run_sync(services.ingestion.list_documents)
    return {"documents": [document_to_dict(d) for d in documents], "count": len(documents)}


@router.get("/documents/{document_id}")
async def get_document(document_id: str, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    return document_to_dict(await anyio.to_thread.run_sync(services.ingestion.get_document, document_id))


@router.get("/documents/{document_id}/versions")
async def list_versions(document_id: str, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    versions = await anyio.to_thread.run_sync(services.ingestion.list_versions, document_id)
    return {"document_id": document_id, "versions": [version_to_dict(v) for v in versions]}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Delete a document with all its versions, vectors and stored files."""
    await anyio.to_thread.run_sync(services.ingestion.delete, document_id)
    return {"deleted": True, "document_id": document_id}


@router.post("/documents/reindex")
async def reindex_all(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Rebuild every document's index with the current embedding model and chunking settings."""
    summary = await anyio.to_thread.run_sync(services.ingestion.reindex_all)
    return reindex_summary_to_dict(summary)


@router.post("/documents/{document_id}/reindex")
async def reindex_document(document_id: str, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    result = await anyio.to_thread.run_sync(services.ingestion.reindex_document, document_id)
    return ingest_result_to_dict(result)


@router.get("/index/status")
async def index_status(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    """Counts and a consistency check between the database and the vector index."""
    report = await anyio.to_thread.run_sync(services.ingestion.index_report)
    return {"ready": services.ingestion.ready, **index_report_to_dict(report)}
