"""
LLM error taxonomy.

Every provider translates its own failures into these types, so the rest of the
application (retry, fallback, API layer) can reason about them without knowing
which provider produced them.

* ``retryable``         - the same request may succeed if repeated shortly.
* ``fallback_eligible`` - switching to a different provider may help.

Messages are redacted and truncated before being stored: upstream error text may
echo parts of a request, and credentials must never reach logs or clients.
"""

from __future__ import annotations

import math

from app.core.errors import AppError
from app.core.logging import redact

MAX_MESSAGE_LENGTH = 300


def _safe_message(message: str) -> str:
    return redact(str(message).strip())[:MAX_MESSAGE_LENGTH]


class LLMError(AppError):
    status_code = 502
    code = "llm_error"
    retryable = False
    fallback_eligible = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        retry_after: float | None = None,
        upstream_status: int | None = None,
    ) -> None:
        details = {
            key: value
            for key, value in {
                "provider": provider,
                "model": model,
                "retry_after_seconds": retry_after,
                "upstream_status": upstream_status,
            }.items()
            if value is not None
        }
        headers = {"Retry-After": str(max(1, math.ceil(retry_after)))} if retry_after is not None else None
        super().__init__(_safe_message(message), details=details, headers=headers)
        self.provider = provider
        self.model = model
        self.retry_after = retry_after
        self.upstream_status = upstream_status
        self.attempts = 1   # updated by the retry helpers


# --- configuration problems -------------------------------------------------


class ProviderNotFoundError(LLMError):
    status_code = 404
    code = "provider_not_found"


class ProviderNotConfiguredError(LLMError):
    status_code = 409
    code = "provider_not_configured"
    fallback_eligible = True


class ModelNotSelectedError(LLMError):
    status_code = 422
    code = "model_not_selected"


# --- upstream failures ------------------------------------------------------


class AuthenticationError(LLMError):
    status_code = 502
    code = "provider_auth_failed"
    fallback_eligible = True


class RateLimitError(LLMError):
    status_code = 429
    code = "provider_rate_limited"
    retryable = True
    fallback_eligible = True


class QuotaExceededError(LLMError):
    status_code = 429
    code = "provider_quota_exceeded"
    fallback_eligible = True


class ProviderUnavailableError(LLMError):
    status_code = 503
    code = "provider_unavailable"
    retryable = True
    fallback_eligible = True


class ProviderTimeoutError(LLMError):
    status_code = 504
    code = "provider_timeout"
    retryable = True
    fallback_eligible = True


class ModelNotFoundError(LLMError):
    status_code = 404
    code = "model_not_found"


class InvalidRequestError(LLMError):
    status_code = 422
    code = "provider_rejected_request"


class InvalidResponseError(LLMError):
    status_code = 502
    code = "provider_invalid_response"
    fallback_eligible = True


class StreamInterruptedError(LLMError):
    status_code = 502
    code = "stream_interrupted"
