"""Shared helpers for MCP tool implementations.

Every tool:
  * declares a JSON input schema (used by the MCP server's list_tools and for
    argument validation),
  * validates its arguments before touching business logic,
  * returns a structured dict with a consistent shape.

Standard response envelope:
  { "ok": bool, "data": <any> | None, "error": <code> | None,
    "message": <human string> }
"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "") -> dict:
    return {"ok": True, "data": data, "error": None, "message": message}


def err(error: str, message: str = "") -> dict:
    return {"ok": False, "data": None, "error": error, "message": message or error}


def require_string(args: dict, key: str) -> tuple[str | None, dict | None]:
    """Validate that ``args[key]`` is a non-empty string.

    Returns (value, None) on success or (None, error_response) on failure.
    """
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return None, err(
            "invalid_arguments", f"'{key}' is required and must be a non-empty string."
        )
    return value.strip(), None
