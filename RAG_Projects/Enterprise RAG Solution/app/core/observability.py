"""
Observability: request IDs, an access log line per request, and Prometheus-format metrics.

Implemented as a pure ASGI middleware so streaming (SSE) responses are never buffered. Metrics are kept
in memory (per process) and exposed at ``/metrics`` in the Prometheus text format; there is no external
dependency. Labels use the route *template* (``/api/v1/documents/{document_id}``), never the raw path, so
the number of series stays bounded.
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
import uuid
from collections import defaultdict

logger = logging.getLogger("app.http")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(names: tuple[str, ...], values: tuple[str, ...]) -> str:
    return "{" + ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values)) + "}" if names else ""


class Metrics:
    """A tiny thread-safe registry: labelled counters and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, tuple[str, tuple[str, ...], dict[tuple[str, ...], float]]] = {}
        self._histograms: dict[str, tuple[str, tuple[str, ...], dict[tuple[str, ...], list[float]]]] = {}
        self._gauges: dict[str, tuple[str, float]] = {}
        self.started = time.time()

    def counter(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        with self._lock:
            self._counters.setdefault(name, (help_text, labels, defaultdict(float)))

    def histogram(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        with self._lock:
            self._histograms.setdefault(name, (help_text, labels, {}))

    def inc(self, name: str, *label_values: str, amount: float = 1.0) -> None:
        with self._lock:
            _, labels, series = self._counters[name]
            if len(label_values) != len(labels):
                raise ValueError(f"{name} expects labels {labels}")
            series[tuple(label_values)] += amount

    def observe(self, name: str, value: float, *label_values: str) -> None:
        with self._lock:
            _, labels, series = self._histograms[name]
            if len(label_values) != len(labels):
                raise ValueError(f"{name} expects labels {labels}")
            data = series.setdefault(tuple(label_values), [0.0] * (len(DURATION_BUCKETS) + 2))   # buckets..., +Inf, sum
            for i, bound in enumerate(DURATION_BUCKETS):
                if value <= bound:
                    data[i] += 1
            data[len(DURATION_BUCKETS)] += 1      # +Inf bucket == count
            data[len(DURATION_BUCKETS) + 1] += value

    def set_gauge(self, name: str, help_text: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = (help_text, value)

    def counter_value(self, name: str, *label_values: str) -> float:
        with self._lock:
            return self._counters[name][2].get(tuple(label_values), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, (help_text, labels, series) in sorted(self._counters.items()):
                lines += [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
                for values, count in sorted(series.items()):
                    lines.append(f"{name}{_labels(labels, values)} {count:g}")
            for name, (help_text, labels, series) in sorted(self._histograms.items()):
                lines += [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
                for values, data in sorted(series.items()):
                    base = list(zip(labels, values))
                    for i, bound in enumerate(DURATION_BUCKETS):
                        lab = ",".join(f'{n}="{_escape(v)}"' for n, v in [*base, ("le", f"{bound:g}")])
                        lines.append(f"{name}_bucket{{{lab}}} {data[i]:g}")
                    lab = ",".join(f'{n}="{_escape(v)}"' for n, v in [*base, ("le", "+Inf")])
                    lines.append(f"{name}_bucket{{{lab}}} {data[len(DURATION_BUCKETS)]:g}")
                    lines.append(f"{name}_sum{_labels(labels, values)} {data[len(DURATION_BUCKETS) + 1]:.6f}")
                    lines.append(f"{name}_count{_labels(labels, values)} {data[len(DURATION_BUCKETS)]:g}")
            for name, (help_text, value) in sorted(self._gauges.items()):
                lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {value:g}"]
        return "\n".join(lines) + "\n"


def new_metrics() -> Metrics:
    metrics = Metrics()
    metrics.counter("http_requests_total", "HTTP requests handled.", ("method", "route", "status"))
    metrics.histogram("http_request_duration_seconds", "Time to send the complete response.", ("method", "route"))
    metrics.counter("http_rate_limited_total", "Requests rejected by the rate limiter.", ("bucket",))
    metrics.counter("http_unhandled_errors_total", "Requests that ended in an unhandled exception.", ())
    return metrics


def _route_template(scope: dict) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    raw = scope.get("path", "")
    if raw.startswith("/static"):
        return "/static"
    return "unmatched"


def client_address(scope: dict, headers: dict[bytes, bytes], trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    client = scope.get("client")
    return client[0] if client else "unknown"


class ObservabilityMiddleware:
    """Request id + access log + metrics around the whole application."""

    QUIET_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})

    def __init__(self, app, metrics: Metrics, *, trust_proxy: bool = False) -> None:
        self.app = app
        self.metrics = metrics
        self.trust_proxy = trust_proxy

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = {"code": 500}
        finished = {"done": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished["done"] = True
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            failed = True
            self.metrics.inc("http_unhandled_errors_total")
            raise
        finally:
            duration = time.perf_counter() - started
            route = _route_template(scope)
            method = scope.get("method", "?")
            code = str(status["code"])
            self.metrics.inc("http_requests_total", method, route, code)
            self.metrics.observe("http_request_duration_seconds", duration, method, route)
            path = scope.get("path", "")
            if path not in self.QUIET_PATHS:
                logger.info(
                    "request method=%s path=%s route=%s status=%s duration_ms=%.1f client=%s%s",
                    method, path, route, code, duration * 1000,
                    client_address(scope, headers, self.trust_proxy),
                    "" if finished["done"] or failed else " incomplete=true",
                )
            request_id_var.reset(token)


def install_request_id_logging() -> None:
    """Make every log record carry ``request_id`` (used by the log format)."""
    old_factory = logging.getLogRecordFactory()
    if getattr(old_factory, "_rag_request_id", False):
        return

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    factory._rag_request_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
