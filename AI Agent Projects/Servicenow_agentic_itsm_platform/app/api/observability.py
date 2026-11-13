"""Structured logging, request IDs and secret masking for log output."""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.security.sanitize import mask_secrets

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("app.access")


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.msg = mask_secrets(record.getMessage())
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                   "logger": record.name, "request_id": getattr(record, "request_id", "-"),
                   "message": record.getMessage()}
        if record.exc_info:
            payload["exception"] = mask_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_Context())
    handler.setFormatter(JsonFormatter() if settings.log_json else logging.Formatter(
        "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "chromadb", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, echoes it in X-Request-ID and writes one access-log line."""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            access_log.info("%s %s -> %s in %.1fms", request.method, request.url.path, response.status_code,
                            (time.perf_counter() - started) * 1000)
            return response
        finally:
            request_id_var.reset(token)
