"""Request ids, access logs, metrics, health endpoints and rate limiting."""

import logging
import re

import pytest

from app.core.observability import Metrics, new_metrics
from app.core.ratelimit import SlidingWindowLimiter, classify
from tests.conftest import make_stack


@pytest.fixture
def ops(tmp_path):
    instance = make_stack(tmp_path)
    yield instance
    instance.close()


# ---------------------------------------------------------------- request id + access log


def test_every_response_has_a_request_id_and_a_supplied_one_is_kept(ops):
    generated = ops.client.get("/api/status").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    assert ops.client.get("/api/status", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"


def test_unsafe_request_ids_are_replaced(ops):
    for bad in ["short", "has spaces in it!", "x" * 200, "line\tbreak-1234"]:
        got = ops.client.get("/api/status", headers={"X-Request-ID": bad}).headers["x-request-id"]
        assert got != bad and re.fullmatch(r"[0-9a-f]{32}", got)


class _Capture(logging.Handler):
    """The app loggers do not propagate to the root logger, so attach to them directly."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def http_log():
    handler = _Capture()
    logger = logging.getLogger("app.http")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(previous)


def test_access_log_has_route_status_and_request_id_but_no_query_string(ops, http_log):
    ops.client.get("/api/v1/documents?secret=hunter2", headers={"X-Request-ID": "trace-log-12345"})
    line = next(r for r in http_log.records if r.name == "app.http")
    text = line.getMessage()
    assert "route=/api/v1/documents" in text and "status=200" in text and "method=GET" in text
    assert "hunter2" not in text and line.request_id == "trace-log-12345"


def test_health_and_metrics_requests_are_not_logged(ops, http_log):
    ops.client.get("/health/live")
    ops.client.get("/metrics")
    assert not http_log.records


# ---------------------------------------------------------------- health


def test_liveness_and_readiness(ops):
    assert ops.client.get("/health/live").json() == {"status": "ok"}
    assert ops.client.get("/health/ready").json() == {"status": "ready"}


def test_readiness_is_503_when_startup_failed(tmp_path):
    from tests.unit.test_app_stack import BrokenEmbedder

    stack = make_stack(tmp_path, embedder=BrokenEmbedder())
    try:
        response = stack.client.get("/health/ready")
        assert response.status_code == 503 and response.json() == {"status": "starting"}
        assert stack.client.get("/health/live").status_code == 200
    finally:
        stack.close()


# ---------------------------------------------------------------- metrics


def test_metrics_count_requests_by_route_template_not_raw_path(ops):
    ops.client.get("/api/v1/documents/" + "a" * 32)           # 404, unknown id
    ops.client.get("/api/v1/documents/" + "b" * 32)
    body = ops.client.get("/metrics")
    assert body.headers["content-type"].startswith("text/plain")
    text = body.text
    assert 'http_requests_total{method="GET",route="/api/v1/documents/{document_id}",status="404"} 2' in text
    assert "a" * 32 not in text
    assert 'http_request_duration_seconds_count{method="GET",route="/api/v1/documents/{document_id}"} 2' in text
    assert 'http_request_duration_seconds_bucket{method="GET",route="/api/v1/documents/{document_id}",le="+Inf"} 2' in text


def test_metrics_include_knowledge_base_gauges(ops):
    ops.client.post("/api/v1/documents", files={"file": ("a.txt", b"The quick brown fox jumps over the lazy dog today.", "text/plain")})
    text = ops.client.get("/metrics").text
    assert "rag_ready 1" in text and "rag_documents 1" in text and "rag_index_consistent 1" in text


def test_metrics_can_be_disabled(tmp_path):
    stack = make_stack(tmp_path, metrics_enabled=False)
    try:
        assert stack.client.get("/metrics").status_code == 404
    finally:
        stack.close()


def test_metrics_contain_no_user_content(ops):
    ops.client.post("/api/query", json={"query": "my-private-question-text"})
    assert "my-private-question-text" not in ops.client.get("/metrics").text


def test_label_values_are_escaped():
    metrics = Metrics()
    metrics.counter("x_total", "x", ("a",))
    metrics.inc("x_total", 'q"uote\nline')
    assert 'x_total{a="q\\"uote\\nline"} 1' in metrics.render()


def test_wrong_label_count_is_a_programming_error():
    metrics = new_metrics()
    with pytest.raises(ValueError):
        metrics.inc("http_requests_total", "GET")


# ---------------------------------------------------------------- limiter unit tests


def test_classification():
    assert classify("POST", "/api/v1/chat") == "llm"
    assert classify("POST", "/api/query") == "llm"
    assert classify("POST", "/api/v1/llm/providers/ollama/test") == "llm"
    for method, path in [("POST", "/api/v1/documents"), ("DELETE", "/api/v1/documents/x"), ("PUT", "/api/v1/llm/config"),
                         ("POST", "/api/upload"), ("DELETE", "/api/documents/a.txt")]:
        assert classify(method, path) == "write"
    for method, path in [("GET", "/api/documents"), ("GET", "/api/status"), ("GET", "/"), ("GET", "/static/app.js"),
                         ("GET", "/api/v1/llm/providers"), ("GET", "/metrics"), ("POST", "/health/ready"), ("OPTIONS", "/api/v1/chat")]:
        assert classify(method, path) is None


def test_sliding_window_allows_then_blocks_then_recovers():
    now = [0.0]
    limiter = SlidingWindowLimiter({"llm": 3}, clock=lambda: now[0])
    assert [limiter.check("a", "llm")[0] for _ in range(3)] == [True, True, True]
    allowed, remaining, retry = limiter.check("a", "llm")
    assert (allowed, remaining) == (False, 0) and retry == 60
    now[0] = 30.0
    assert limiter.check("a", "llm")[2] == 30            # oldest hit leaves the window in 30s
    now[0] = 60.0
    assert limiter.check("a", "llm")[0] is True


def test_clients_and_buckets_are_independent_and_zero_means_unlimited():
    limiter = SlidingWindowLimiter({"llm": 1, "write": 0})
    assert limiter.check("a", "llm")[0] and not limiter.check("a", "llm")[0]
    assert limiter.check("b", "llm")[0]
    assert all(limiter.check("a", "write")[0] for _ in range(100))
    assert limiter.check("a", "unknown")[0]


def test_limiter_memory_is_bounded(monkeypatch):
    import app.core.ratelimit as rl

    monkeypatch.setattr(rl, "MAX_TRACKED_KEYS", 50)
    limiter = SlidingWindowLimiter({"llm": 5})
    for i in range(500):
        limiter.check(f"client{i}", "llm")
    assert len(limiter._hits) <= 50


# ---------------------------------------------------------------- limiter in the real app


def test_chat_is_limited_with_retry_after_and_ui_compatible_body(tmp_path):
    stack = make_stack(tmp_path, rate_limit_llm_per_minute=2)
    try:
        codes = [stack.client.post("/api/query", json={"query": "anything at all"}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        blocked = stack.client.post("/api/query", json={"query": "anything at all"})
        assert blocked.status_code == 429 and blocked.headers["retry-after"].isdigit()
        body = blocked.json()
        assert "Too many requests" in body["detail"] and body["error"]["code"] == "rate_limited"
        assert blocked.headers["x-request-id"]                                  # still observable
        assert 'http_rate_limited_total{bucket="llm"} 2' in stack.client.get("/metrics").text
        first = stack.client.post("/api/v1/chat", json={"question": "x"})        # shares the llm bucket
        assert first.status_code == 429
    finally:
        stack.close()


def test_reads_and_polling_are_never_limited(tmp_path):
    stack = make_stack(tmp_path, rate_limit_llm_per_minute=1, rate_limit_write_per_minute=1)
    try:
        assert all(stack.client.get("/api/documents").status_code == 200 for _ in range(40))
        assert all(stack.client.get("/api/status").status_code == 200 for _ in range(40))
        assert all(stack.client.get("/").status_code == 200 for _ in range(10))
    finally:
        stack.close()


def test_writes_are_limited_separately_from_chat(tmp_path):
    stack = make_stack(tmp_path, rate_limit_write_per_minute=2, rate_limit_llm_per_minute=100)
    try:
        def upload(i):
            return stack.client.post("/api/v1/documents", files={"file": (f"d{i}.txt", f"Document number {i} has unique text inside it.".encode(), "text/plain")})
        assert [upload(i).status_code for i in range(3)] == [201, 201, 429]
        assert stack.client.post("/api/query", json={"query": "unique text"}).status_code == 200
    finally:
        stack.close()


def test_rate_limiting_can_be_disabled(tmp_path):
    stack = make_stack(tmp_path, rate_limit_enabled=False, rate_limit_llm_per_minute=1)
    try:
        assert all(stack.client.post("/api/query", json={"query": "anything at all"}).status_code == 200 for _ in range(5))
    finally:
        stack.close()


def test_forwarded_header_is_ignored_unless_proxy_is_trusted(tmp_path):
    untrusted = make_stack(tmp_path / "u", rate_limit_llm_per_minute=1)
    try:
        codes = [untrusted.client.post("/api/query", json={"query": "anything at all"}, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(2)]
        assert codes == [200, 429]          # spoofing the header gives no extra budget
    finally:
        untrusted.close()
    trusted = make_stack(tmp_path / "t", rate_limit_llm_per_minute=1, trust_proxy_headers=True)
    try:
        codes = [trusted.client.post("/api/query", json={"query": "anything at all"}, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(2)]
        assert codes == [200, 200]
    finally:
        trusted.close()


def test_streaming_responses_pass_through_the_middleware(tmp_path):
    stack = make_stack(tmp_path)
    try:
        stack.client.post("/api/v1/documents", files={"file": ("a.txt", b"The Zorbulon budget for 2031 is four million credits in total.", "text/plain")})
        # No LLM is configured in tests, so the stream request ends in a normal error; the point is that
        # the middleware stack handles the request and still adds its headers.
        response = stack.client.post("/api/v1/chat", json={"question": "Zorbulon budget", "stream": True})
        assert "x-request-id" in response.headers and "x-ratelimit-limit" in response.headers
    finally:
        stack.close()
