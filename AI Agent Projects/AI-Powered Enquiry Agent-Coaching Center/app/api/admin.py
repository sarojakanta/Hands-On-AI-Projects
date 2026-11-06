"""Admin JSON API (auth-protected, tenant = admin's institute)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_admin
from app.config import get_settings
from app.config.logging_config import get_logger
from app.database.session import get_db
from app.models import AdminUser, KnowledgeDocument
from app.rag.ingestion import ingest_document
from app.rag.loaders import SUPPORTED_EXTENSIONS
from app.rag.vector_store import get_vector_store
from app.schemas.course import CourseCreate, CourseOut, CourseUpdate
from app.schemas.lead import FollowUpOut, LeadOut, LeadUpdate
from app.services import (
    conversation_service,
    course_service,
    dashboard_service,
    institute_service,
    lead_service,
)
from app.services.exceptions import NotFoundError, ValidationError

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = get_logger(__name__)
settings = get_settings()


# --- Institute / embed info ----------------------------------------------
@router.get("/institute")
def institute_info(
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return the logged-in admin's institute details, including the PUBLIC
    api_key and a ready-to-use base URL, so the dashboard can show the embed
    snippet. This is auth-protected (only staff see their own key here)."""
    inst = institute_service.get_by_id(db, admin.institute_id)
    return {
        "id": inst.id,
        "name": inst.name,
        "slug": inst.slug,
        "api_key": inst.api_key,
        "base_url": str(request.base_url),
    }


# --- Stats ----------------------------------------------------------------
@router.get("/stats")
def stats(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    data = dashboard_service.get_stats(db, admin.institute_id)
    data["course_wise"] = dashboard_service.course_wise_enquiries(db, admin.institute_id)
    return data


# --- Leads ----------------------------------------------------------------
@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    status_filter: str | None = None,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return lead_service.list_leads(db, admin.institute_id, status=status_filter)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        return lead_service.update_lead(db, admin.institute_id, lead_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/followups", response_model=list[FollowUpOut])
def list_followups(
    status_filter: str | None = None,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return lead_service.list_followups(db, admin.institute_id, status=status_filter)


# --- Conversations --------------------------------------------------------
@router.get("/conversations")
def list_conversations(
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)
):
    convos = conversation_service.list_conversations(db, admin.institute_id)
    return [
        {
            "id": c.id,
            "session_id": c.session_id,
            "status": c.status,
            "lead_id": c.lead_id,
            "created_at": c.created_at,
            "message_count": len(c.messages),
        }
        for c in convos
    ]


@router.get("/conversations/{session_id}")
def get_conversation(
    session_id: str,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        convo = conversation_service.get_conversation(db, admin.institute_id, session_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {
        "id": convo.id,
        "session_id": convo.session_id,
        "status": convo.status,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in convo.messages
        ],
    }


# --- Courses --------------------------------------------------------------
@router.get("/courses", response_model=list[CourseOut])
def list_courses(
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)
):
    return course_service.list_courses(db, admin.institute_id, active_only=False)


@router.post("/courses", response_model=CourseOut, status_code=status.HTTP_201_CREATED)
def create_course(
    payload: CourseCreate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return course_service.create_course(db, admin.institute_id, payload)


@router.patch("/courses/{course_id}", response_model=CourseOut)
def update_course(
    course_id: int,
    payload: CourseUpdate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        return course_service.update_course(db, admin.institute_id, course_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        course_service.delete_course(db, admin.institute_id, course_id)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


# --- Knowledge documents --------------------------------------------------
@router.get("/documents")
def list_documents(
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)
):
    docs = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.institute_id == admin.institute_id)
        .order_by(KnowledgeDocument.created_at.desc())
        .all()
    )
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "title": d.title,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "error": d.error,
            "created_at": d.created_at,
        }
        for d in docs
    ]


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Upload a knowledge document, store it, and ingest it into the vector store."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type '{ext}'. Allowed: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    # Store under knowledge_base/uploads/<institute_id>/ (runtime uploads).
    upload_dir = os.path.join(settings.knowledge_base_dir, "uploads", str(admin.institute_id))
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = os.path.basename(file.filename or f"document{ext}")
    stored_path = os.path.join(upload_dir, safe_name)

    content = await file.read()
    # Basic size guard: 10 MB.
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File too large (max 10 MB).")
    with open(stored_path, "wb") as fh:
        fh.write(content)

    doc = KnowledgeDocument(
        institute_id=admin.institute_id,
        filename=safe_name,
        stored_path=stored_path,
        content_type=file.content_type,
        title=title or safe_name.rsplit(".", 1)[0].replace("_", " ").title(),
        status="pending",
        chunk_count=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        count = ingest_document(db, doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Uploaded but failed to index: {exc}",
        ) from exc

    return {"id": doc.id, "filename": doc.filename, "status": doc.status, "chunks": count}


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.institute_id == admin.institute_id,
        )
        .first()
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    # Remove vectors, the stored file, and the row.
    get_vector_store().delete_document(admin.institute_id, doc.id)
    try:
        if os.path.isfile(doc.stored_path):
            os.remove(doc.stored_path)
    except OSError:
        logger.warning("Could not remove file %s", doc.stored_path)
    db.delete(doc)
    db.commit()
