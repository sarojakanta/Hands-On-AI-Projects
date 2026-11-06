"""Tests for the public chat rate limiter."""
from __future__ import annotations

from app.auth.rate_limit import SlidingWindowLimiter, public_chat_limiter


def test_sliding_window_allows_then_blocks():
    lim = SlidingWindowLimiter(max_requests=3, window_seconds=60)
    assert [lim.allow("ip") for _ in range(5)] == [True, True, True, False, False]


def test_sliding_window_keys_are_independent():
    lim = SlidingWindowLimiter(max_requests=1, window_seconds=60)
    assert lim.allow("a") is True
    assert lim.allow("b") is True
    assert lim.allow("a") is False


def test_chat_enforces_rate_limit(client, institute, monkeypatch):
    # Shrink the live limiter for this test, then restore via reset.
    monkeypatch.setattr(public_chat_limiter, "max_requests", 3)
    public_chat_limiter.reset()
    hdr = {"X-Institute-Key": "test-key-institute-a"}
    codes = []
    for _ in range(5):
        r = client.post("/api/chat", json={"message": "hi"}, headers=hdr)
        codes.append(r.status_code)
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    public_chat_limiter.reset()
