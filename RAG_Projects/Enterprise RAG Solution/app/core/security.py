"""
Security helpers shared across the application.

* ``validate_filename`` / ``resolve_inside`` protect against path traversal
  for user-supplied file names (works for both ``/`` and ``\\`` separators,
  regardless of the operating system the server runs on).
* ``mask_secret`` produces a safe representation of credentials for logs/UI.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

MAX_FILENAME_LENGTH = 255

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


class InvalidFilenameError(ValueError):
    """Raised when a user-supplied file name is unsafe or unsupported."""


def validate_filename(name: str, allowed_extensions: Iterable[str]) -> str:
    """
    Validate a user-supplied file name and return it unchanged if it is safe.

    The name must be a plain file name: no path separators, no ``..``, no drive
    or stream markers (``:``), no control characters, no leading dot and not a
    reserved Windows device name. The extension is compared case-insensitively
    against ``allowed_extensions`` (for example ``{".txt"}``).

    Names are rejected rather than silently rewritten so callers never end up
    storing a file under a different name than the one the user chose.
    """
    if not name or not name.strip():
        raise InvalidFilenameError("File name is required.")
    name = name.strip()

    if len(name) > MAX_FILENAME_LENGTH:
        raise InvalidFilenameError("File name is too long.")
    if _CONTROL_CHARS.search(name):
        raise InvalidFilenameError("File name contains control characters.")
    if "/" in name or "\\" in name:
        raise InvalidFilenameError("File name must not contain path separators.")
    if ":" in name:
        raise InvalidFilenameError("File name must not contain ':'.")
    if ".." in name or name.startswith("."):
        raise InvalidFilenameError("File name must not contain '..' or start with '.'.")

    suffix = Path(name).suffix.lower()
    allowed = {ext.lower() for ext in allowed_extensions}
    if suffix not in allowed:
        supported = ", ".join(sorted(allowed))
        raise InvalidFilenameError(f"Unsupported file type. Supported types: {supported}.")

    if Path(name).stem.upper() in _WINDOWS_RESERVED:
        raise InvalidFilenameError("File name is reserved by the operating system.")

    return name


def resolve_inside(base_dir: Path, name: str) -> Path:
    """
    Join ``name`` onto ``base_dir`` and guarantee the result stays directly
    inside ``base_dir`` (defence in depth on top of ``validate_filename``).
    """
    base = base_dir.resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise InvalidFilenameError("Resolved path is outside the allowed directory.")
    return target


def mask_secret(value: str | None, visible: int = 4) -> str:
    """Return a masked form of a credential, e.g. ``'****abcd'`` (never the full value)."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return "*" * 4 + value[-visible:]
