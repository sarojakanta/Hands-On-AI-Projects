"""Incremental parsers for streamed provider responses (Server-Sent Events and NDJSON)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.llm.errors import InvalidResponseError


@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """
    Parse an SSE stream given its decoded lines.

    Follows the SSE format: ``event:`` / ``data:`` fields, events separated by a blank
    line, ``:`` comment lines ignored, multi-line ``data`` joined with ``\\n``.
    Unknown fields are ignored (providers add new ones over time).
    """
    event: str | None = None
    data_lines: list[str] = []

    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                yield SSEEvent(event, "\n".join(data_lines))
            event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:   # stream ended without a trailing blank line
        yield SSEEvent(event, "\n".join(data_lines))


async def iter_ndjson(lines: AsyncIterator[str], *, provider: str) -> AsyncIterator[dict[str, Any]]:
    """Parse newline-delimited JSON objects, skipping blank lines."""
    async for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise InvalidResponseError(
                f"{provider} returned a malformed stream chunk.", provider=provider
            ) from exc
        if isinstance(value, dict):
            yield value


def parse_json_object(text: str, *, provider: str) -> dict[str, Any]:
    """Parse a JSON payload that must be an object; raise ``InvalidResponseError`` otherwise."""
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise InvalidResponseError(f"{provider} returned invalid JSON.", provider=provider) from exc
    if not isinstance(value, dict):
        raise InvalidResponseError(f"{provider} returned an unexpected payload.", provider=provider)
    return value
