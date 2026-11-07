"""
Retry helpers shared by every provider.

* Only errors flagged ``retryable`` are retried (rate limits, timeouts, 5xx, connection errors).
* A server-provided ``Retry-After`` is honoured, but never waited on for longer than
  ``max_retry_after``; in that case the error is raised immediately so a fallback
  provider can take over instead of blocking the user.
* Streams are retried only while nothing has been emitted yet. Once the caller has
  seen output, a failure surfaces as ``StreamInterruptedError`` (text cannot be spliced).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.llm.errors import LLMError, StreamInterruptedError

T = TypeVar("T")

SleepFn = Callable[[float], Awaitable[None]]
RetryHook = Callable[[int, LLMError, float], None]


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25            # random extra, as a fraction of the computed delay
    max_retry_after: float = 20.0   # longest wait we accept when the server says "retry after N seconds"

    def delay_for(self, attempt: int, error: LLMError) -> float | None:
        """Seconds to wait before the next try, or ``None`` if we should give up now."""
        if error.retry_after is not None:
            return error.retry_after if error.retry_after <= self.max_retry_after else None
        delay = min(self.base_delay * (2**attempt), self.max_delay)
        return delay + random.uniform(0, delay * self.jitter)


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: SleepFn = asyncio.sleep,
    on_retry: RetryHook | None = None,
) -> tuple[T, int]:
    """Run ``operation`` with retries. Returns ``(result, attempts_used)``."""
    attempt = 0
    while True:
        try:
            return await operation(), attempt + 1
        except LLMError as exc:
            exc.attempts = attempt + 1
            if not exc.retryable or attempt >= policy.max_retries:
                raise
            delay = policy.delay_for(attempt, exc)
            if delay is None:
                raise
            if on_retry:
                on_retry(attempt + 1, exc, delay)
            await sleep(delay)
            attempt += 1


async def stream_with_retries(
    factory: Callable[[], AsyncIterator[T]],
    policy: RetryPolicy,
    *,
    sleep: SleepFn = asyncio.sleep,
    on_retry: RetryHook | None = None,
) -> AsyncIterator[T]:
    """Yield items from ``factory()``, retrying only if the failure happens before the first item."""
    attempt = 0
    while True:
        emitted = False
        try:
            async for item in factory():
                emitted = True
                yield item
            return
        except LLMError as exc:
            exc.attempts = attempt + 1
            if emitted:
                raise StreamInterruptedError(
                    f"Stream interrupted after partial output: {exc.message}",
                    provider=exc.provider,
                    model=exc.model,
                ) from exc
            if not exc.retryable or attempt >= policy.max_retries:
                raise
            delay = policy.delay_for(attempt, exc)
            if delay is None:
                raise
            if on_retry:
                on_retry(attempt + 1, exc, delay)
            await sleep(delay)
            attempt += 1
