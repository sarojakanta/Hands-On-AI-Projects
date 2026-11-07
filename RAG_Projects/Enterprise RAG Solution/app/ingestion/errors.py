"""Errors raised by document ingestion (all render as structured API errors)."""

from __future__ import annotations

from app.core.errors import AppError, NotFoundError


class InvalidDocumentError(AppError):
    status_code = 400
    code = "invalid_document"


class DocumentTooLargeError(AppError):
    status_code = 413
    code = "document_too_large"


class DuplicateDocumentError(AppError):
    """The same content is already stored under a different document name."""

    status_code = 409
    code = "duplicate_document"


class DocumentNotFoundError(NotFoundError):
    code = "document_not_found"


class IngestionFailedError(AppError):
    status_code = 500
    code = "ingestion_failed"
