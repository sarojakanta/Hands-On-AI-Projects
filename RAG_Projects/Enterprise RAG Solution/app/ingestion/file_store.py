"""
Storage for the original uploaded files: ``<root>/<document_id>/v<version>/<name>``.

Paths are always built from ids the application generated and from a validated file name, and every
resolved path is checked to stay inside the root, so user input can never address another location.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.security import InvalidFilenameError, resolve_inside


class FileStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _version_dir(self, document_id: str, version_number: int) -> Path:
        directory = self._root / document_id / f"v{int(version_number)}"
        if not document_id.isalnum():   # ids are uuid hex; anything else is a bug or an attack
            raise InvalidFilenameError("Invalid document id.")
        return directory

    def save(self, document_id: str, version_number: int, name: str, data: bytes) -> str:
        """Write the original bytes and return the path relative to the root (stored in the database)."""
        directory = self._version_dir(document_id, version_number)
        directory.mkdir(parents=True, exist_ok=True)
        target = resolve_inside(directory, name)
        target.write_bytes(data)
        return target.relative_to(self._root.resolve()).as_posix()

    def read(self, relative_path: str) -> bytes:
        root = self._root.resolve()
        target = (root / relative_path).resolve()
        if root not in target.parents:
            raise InvalidFilenameError("Stored path is outside the upload directory.")
        return target.read_bytes()

    def remove_version(self, document_id: str, version_number: int) -> None:
        shutil.rmtree(self._version_dir(document_id, version_number), ignore_errors=True)

    def remove_document(self, document_id: str) -> None:
        if not document_id.isalnum():
            raise InvalidFilenameError("Invalid document id.")
        shutil.rmtree(self._root / document_id, ignore_errors=True)
