"""Reporting and metrics helpers for the multi-agent pipeline.

This module isolates observability concerns (token accounting, per-agent
timing, retry policy, and external API call recording) from the agent logic in
``agents.py``. Keeping these separate makes the agents easier to read and the
metrics independently testable.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx

# Human-friendly labels for each agent/node, shown in the UI and reports.
AGENT_LABELS: dict[str, str] = {
    "supervisor": "Supervisor",
    "web_search": "Web Search",
    "weather": "Weather",
    "formatter": "Result Formatter",
}

# Maximum attempts for a single LLM request (initial try + retries).
MAX_LLM_ATTEMPTS = 3


def extract_usage(payload: Any) -> dict[str, int]:
    """Pull token counts from an OpenRouter response, tolerating absence."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _as_int(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens")) or (prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def aggregate_usage(usage: list[dict[str, Any]]) -> dict[str, int]:
    """Sum per-call token usage into a single total record."""
    return {
        "prompt_tokens": sum(int(u.get("prompt_tokens", 0) or 0) for u in usage),
        "completion_tokens": sum(
            int(u.get("completion_tokens", 0) or 0) for u in usage
        ),
        "total_tokens": sum(int(u.get("total_tokens", 0) or 0) for u in usage),
    }


def should_retry(exc: Exception) -> bool:
    """Retry transient failures (timeouts, 5xx, transport) but not 4xx (e.g. 429)."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def retry_backoff(attempt: int) -> None:
    """Sleep with a simple linear backoff between retry attempts."""
    time.sleep(0.6 * (attempt + 1))


def record_external(
    metrics: dict[str, Any],
    *,
    provider: str,
    operation: str,
    status: str,
    duration_ms: float,
    detail: str = "",
) -> None:
    """Append an external (non-LLM) API call record to the metrics bag."""
    metrics.setdefault("external_api", []).append(
        {
            "provider": provider,
            "operation": operation,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "detail": detail,
        }
    )


def timed_node(
    name: str,
    fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a graph node to record execution time, success, and retry count.

    The wrapped function receives a mutable ``metrics`` dict it can update (the
    LLM helper increments ``metrics["retries"]``; external calls append to
    ``metrics["external_api"]``). On success a performance record is appended to
    the node's state update. On failure the exception propagates so the web
    layer can map it to an error response.
    """

    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {"retries": 0}
        start = time.perf_counter()
        update = fn(state, metrics)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        update["performance"] = update.get("performance", []) + [
            {
                "name": name,
                "label": AGENT_LABELS.get(name, name),
                "duration_ms": elapsed_ms,
                "status": "success",
                "retries": metrics.get("retries", 0),
            }
        ]
        return update

    return wrapped
