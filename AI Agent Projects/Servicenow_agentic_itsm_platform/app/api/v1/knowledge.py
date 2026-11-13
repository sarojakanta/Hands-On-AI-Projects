import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import Services, get_services, require_role
from app.api.errors import ApiError
from app.api.schemas import KnowledgeSearch
from app.config import settings
from app.db.base import session_scope
from app.db.repositories import AuditRepository
from app.knowledge.ingest import MAX_BYTES, SUPPORTED_TYPES, UnsupportedDocumentError
from app.security.rbac import Principal

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]")


def _safe_filename(name: str) -> str:
    base = _SAFE_NAME.sub("_", Path(name or "").name).lstrip(".")  # strips any path components
    if not base or Path(base).suffix.lower() not in SUPPORTED_TYPES:
        raise UnsupportedDocumentError(
            f"Unsupported file type. Allowed: {', '.join(sorted(set(SUPPORTED_TYPES)))}")
    return base[:120]


@router.get("/stats", summary="Index statistics")
def stats(svc: Services = Depends(get_services), _: Principal = Depends(require_role("viewer"))):
    svc.knowledge.ensure_indexed()
    return svc.knowledge.stats()


@router.get("/documents", summary="List indexed documents")
def documents(file_type: str | None = Query(None, pattern="^(md|txt|pdf|docx)$"), limit: int = Query(100, ge=1, le=500),
              offset: int = Query(0, ge=0), svc: Services = Depends(get_services),
              _: Principal = Depends(require_role("viewer"))):
    svc.knowledge.ensure_indexed()
    return {"items": svc.knowledge.list_documents(file_type, limit, offset), "limit": limit, "offset": offset}


@router.post("/documents", status_code=201, summary="Upload and index a document (admin)",
             description="Accepts PDF, DOCX, TXT and Markdown up to 20 MB. Uploading the same file name replaces "
                         "the previous version. Optional `metadata` is a JSON object of scalar values used for filtering.")
async def upload(file: UploadFile = File(...), metadata: str = Form("{}"), svc: Services = Depends(get_services),
                 admin: Principal = Depends(require_role("admin"))):
    name = _safe_filename(file.filename or "")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ApiError(413, "too_large", f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    try:
        meta = json.loads(metadata or "{}")
        if not isinstance(meta, dict):
            raise ValueError
    except ValueError:
        raise ApiError(422, "invalid_metadata", "metadata must be a JSON object") from None
    target_dir = Path(settings.knowledge_dir) / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    previous = target.read_bytes() if target.exists() else None
    target.write_bytes(data)
    try:  # disk is the source of truth, so re-indexing after an embedder change restores uploads too
        result = svc.knowledge.ingest_file(target, source=f"uploads/{name}", metadata=meta, origin="dir")
    except Exception:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)
        raise
    with session_scope() as s:
        AuditRepository(s).record(admin.username, "knowledge.uploaded", "knowledge_document", result["id"],
                                  source=result["source"], status=result["status"])
    return result


@router.delete("/documents/{doc_id}", summary="Delete a document and its vectors (admin)")
def delete_document(doc_id: str, svc: Services = Depends(get_services),
                    admin: Principal = Depends(require_role("admin"))):
    docs = {d["id"]: d for d in svc.knowledge.list_documents(limit=1000)}
    if doc_id not in docs:
        raise KeyError("Document not found")
    source = docs[doc_id]["source"]
    svc.knowledge.delete_document(doc_id)
    path = Path(settings.knowledge_dir) / source
    if docs[doc_id]["origin"] == "dir" and path.is_file() and path.resolve().is_relative_to(
            Path(settings.knowledge_dir).resolve()):
        path.unlink()  # otherwise the next directory sync would resurrect it
    with session_scope() as s:
        AuditRepository(s).record(admin.username, "knowledge.deleted", "knowledge_document", doc_id, source=source)
    return {"deleted": doc_id}


@router.post("/sync", summary="Re-sync the knowledge directory (admin)")
def sync(svc: Services = Depends(get_services), admin: Principal = Depends(require_role("admin"))):
    result = svc.knowledge.sync_directory()
    with session_scope() as s:
        AuditRepository(s).record(admin.username, "knowledge.synced", "knowledge", "directory", **result)
    return result


@router.post("/search", summary="Semantic search returning cited excerpts (no generated answers)")
def search(body: KnowledgeSearch, svc: Services = Depends(get_services), _: Principal = Depends(require_role("viewer"))):
    return {"items": svc.knowledge.search(body.query, limit=body.limit, filters=body.filters or None)}
