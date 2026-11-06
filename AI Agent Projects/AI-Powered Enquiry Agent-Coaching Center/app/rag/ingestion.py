"""Document ingestion pipeline: load -> sanitize -> chunk -> embed -> store."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config.logging_config import get_logger
from app.models import KnowledgeDocument
from app.rag.chunking import split_text
from app.rag.loaders import load_document
from app.rag.sanitize import sanitize_text
from app.rag.vector_store import VectorStore, get_vector_store

logger = get_logger(__name__)


def ingest_document(
    db: Session,
    document: KnowledgeDocument,
    store: VectorStore | None = None,
) -> int:
    """Ingest a single KnowledgeDocument into the vector store.

    Updates the document row status/chunk_count. Returns chunk count.
    Raises on failure after recording the error on the row.
    """
    store = store or get_vector_store()
    try:
        raw = load_document(document.stored_path)
        clean = sanitize_text(raw)
        chunks = split_text(clean)
        if not chunks:
            document.status = "indexed"
            document.chunk_count = 0
            document.error = None
            db.commit()
            logger.warning("Document %s produced no chunks.", document.filename)
            return 0

        # Replace any existing chunks for this document (idempotent re-ingest).
        store.delete_document(document.institute_id, document.id)
        count = store.add_chunks(
            institute_id=document.institute_id,
            document_id=document.id,
            title=document.title or document.filename,
            chunks=chunks,
        )
        document.status = "indexed"
        document.chunk_count = count
        document.error = None
        db.commit()
        logger.info(
            "Indexed %s (%d chunks) for institute %d",
            document.filename,
            count,
            document.institute_id,
        )
        return count
    except Exception as exc:  # noqa: BLE001 - record and re-raise
        db.rollback()
        document.status = "failed"
        document.error = str(exc)[:1000]
        db.commit()
        logger.exception("Failed to ingest %s", document.filename)
        raise


def ingest_institute(
    db: Session, institute_id: int, store: VectorStore | None = None
) -> dict:
    """Ingest all pending/failed documents for an institute.

    Returns a summary dict {documents, chunks, failures}.
    """
    store = store or get_vector_store()
    docs = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.institute_id == institute_id)
        .all()
    )
    total_chunks = 0
    failures = 0
    for doc in docs:
        try:
            total_chunks += ingest_document(db, doc, store=store)
        except Exception:  # noqa: BLE001 - keep going, already logged
            failures += 1
    return {"documents": len(docs), "chunks": total_chunks, "failures": failures}
