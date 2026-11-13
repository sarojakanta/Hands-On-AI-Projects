"""Centralized exception handling: one JSON error shape, no internals leaked."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.observability import request_id_var
from app.approvals import ApprovalPermissionError, ApprovalStateError, ApprovalValidationError
from app.knowledge.embeddings import EmbeddingError
from app.knowledge.ingest import UnsupportedDocumentError
from app.llm.manager import LLMConfigError, LLMError
from app.security.auth import AuthError, SecurityConfigError
from app.security.sanitize import mask_secrets
from app.servicenow.errors import (
    ServiceNowAuthError,
    ServiceNowConfigError,
    ServiceNowError,
    ServiceNowRateLimitError,
    ServiceNowTimeoutError,
)
from app.servicenow.permissions import WriteNotAllowedError
from app.services.investigation import DuplicateInvestigationError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": mask_secrets(message), "request_id": request_id_var.get()}}


def respond(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=body(code, message), headers=headers)


# (exception type, status, code) -- first match wins, so list subclasses before base classes.
_MAP: list[tuple[type, int, str]] = [
    (DuplicateInvestigationError, 409, "duplicate_run"),
    (ApprovalPermissionError, 403, "approval_forbidden"),
    (ApprovalStateError, 409, "approval_state_conflict"),
    (ApprovalValidationError, 422, "approval_invalid"),
    (WriteNotAllowedError, 403, "write_not_allowed"),
    (ServiceNowAuthError, 502, "servicenow_auth_failed"),
    (ServiceNowRateLimitError, 503, "servicenow_rate_limited"),
    (ServiceNowTimeoutError, 504, "servicenow_timeout"),
    (ServiceNowConfigError, 503, "servicenow_not_configured"),
    (ServiceNowError, 502, "servicenow_error"),
    (LLMConfigError, 400, "llm_not_configured"),
    (LLMError, 502, "llm_error"),
    (EmbeddingError, 502, "embedding_error"),
    (UnsupportedDocumentError, 415, "unsupported_document"),
    (SecurityConfigError, 500, "security_misconfigured"),
]


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        return respond(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        return respond(exc.status_code, "http_error", str(exc.detail), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Report field paths and messages only; never echo submitted values (they may be secrets).
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                   for e in exc.errors()]
        content = body("validation_error", "Request validation failed")
        content["error"]["details"] = details
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(AuthError)
    async def _auth(request: Request, exc: AuthError):
        return respond(401, "unauthenticated", str(exc), {"WWW-Authenticate": "Bearer"})

    @app.exception_handler(KeyError)
    async def _not_found(request: Request, exc: KeyError):
        return respond(404, "not_found", str(exc.args[0]) if exc.args else "Not found")

    @app.exception_handler(ValueError)
    async def _bad_value(request: Request, exc: ValueError):
        return respond(400, "bad_request", str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return respond(500, "internal_error", "An internal error occurred. Quote the request ID when reporting it.")

    # Starlette dispatches by exact/MRO class, so register mapped subclasses explicitly.
    for exc_type, status, code in _MAP:
        def make(status=status, code=code):
            async def handler(request: Request, exc: Exception):
                logger.warning("%s: %s", type(exc).__name__, exc)
                return respond(status, code, str(exc))
            return handler
        app.add_exception_handler(exc_type, make())
