"""
Translate HTTP failures from LLM providers into ``LLMError`` subclasses.

The mapping is shared by all HTTP-based providers. It understands the three
error body shapes in use:

* OpenAI / Groq / compatible: ``{"error": {"message": "...", "code": "...", "type": "..."}}``
* Anthropic:                  ``{"type": "error", "error": {"type": "...", "message": "..."}}``
* Ollama:                     ``{"error": "model 'x' not found"}``
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from app.llm.errors import (
    AuthenticationError,
    InvalidRequestError,
    LLMError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)

_QUOTA_HINTS = (
    "insufficient_quota",
    "billing",
    "credit balance",
    "spend limit",
    "exceeded your current quota",
    "quota exceeded",
    "out of credits",
)


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Return the ``Retry-After`` value in seconds (numeric form only), if present."""
    raw = headers.get("retry-after")
    if raw is not None:
        try:
            seconds = float(raw)
            return seconds if seconds >= 0 else None
        except ValueError:
            return None
    raw_ms = headers.get("retry-after-ms")
    if raw_ms is not None:
        try:
            return max(0.0, float(raw_ms) / 1000.0)
        except ValueError:
            return None
    return None


def extract_error(body: str | bytes | None) -> tuple[str, str]:
    """Return ``(message, code_or_type)`` from an error response body."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    text = (body or "").strip()
    if not text:
        return "", ""
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300], ""

    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, str):
            return err, ""
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("detail") or "")
            code = str(err.get("code") or err.get("type") or "")
            return message, code
        if "message" in data:
            return str(data["message"]), str(data.get("code", ""))
    return text[:300], ""


def _looks_like_quota(message: str, code: str) -> bool:
    haystack = f"{message} {code}".lower()
    return any(hint in haystack for hint in _QUOTA_HINTS)


def map_http_error(
    *,
    provider: str,
    model: str | None,
    status: int,
    headers: Mapping[str, str],
    body: str | bytes | None,
) -> LLMError:
    """Build the appropriate ``LLMError`` for an HTTP error response."""
    message, code = extract_error(body)
    description = message or f"HTTP {status}"
    common = {"provider": provider, "model": model, "upstream_status": status}

    if status in (401, 403):
        return AuthenticationError(f"Authentication failed: {description}", **common)

    if status == 402 or (status in (400, 429) and _looks_like_quota(message, code)):
        return QuotaExceededError(f"Quota or billing limit reached: {description}", **common)

    if status == 429:
        return RateLimitError(
            f"Rate limited: {description}",
            retry_after=parse_retry_after(headers),
            **common,
        )

    if status == 404:
        if "model" in description.lower() or "model" in code.lower():
            return ModelNotFoundError(f"Model not found: {description}", **common)
        return InvalidRequestError(
            f"Endpoint not found (check the provider base URL): {description}", **common
        )

    if status in (408, 504):
        return ProviderTimeoutError(f"Provider timed out: {description}", **common)

    if status >= 500:
        return ProviderUnavailableError(f"Provider unavailable: {description}", **common)

    return InvalidRequestError(f"Request rejected: {description}", **common)


def map_transport_error(*, provider: str, model: str | None, exc: Exception, base_url: str) -> LLMError:
    """Translate an ``httpx`` transport-level failure."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(
            f"No response from {provider} within the timeout.", provider=provider, model=model
        )
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return ProviderUnavailableError(
            f"Cannot reach {provider} at {base_url}.", provider=provider, model=model
        )
    return ProviderUnavailableError(
        f"Unexpected transport error talking to {provider}: {type(exc).__name__}",
        provider=provider,
        model=model,
    )
