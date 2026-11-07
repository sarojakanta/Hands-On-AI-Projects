"""
Vector store interface.

The store holds only what is needed for similarity search: an id, the vector, the passage text and
a FLAT metadata dictionary (strings, numbers, booleans). The SQL database is the system of record
(documents, versions, chunks); a vector index can always be rebuilt from it.

Filters are backend-neutral and written once::

    {"version_id": "abc"}                         # equality
    {"version_id": {"in": ["abc", "def"]}}        # membership
    {"document_id": "x", "page": 3}               # several keys are ANDed

Each backend translates them (Chroma ``where`` clauses, SQL ``WHERE`` with bound parameters).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.errors import AppError

Scalar = str | int | float | bool
Filter = Mapping[str, Any]

_KEY = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


class VectorStoreError(AppError):
    status_code = 502
    code = "vector_store_error"


@dataclass(frozen=True)
class VectorRecord:
    id: str
    embedding: Sequence[float]
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    id: str
    score: float                 # cosine similarity, higher is better
    text: str
    metadata: dict[str, Any]


class VectorStore(Protocol):
    name: str

    def ensure_collection(self, collection: str, *, dimension: int, embedding_model: str) -> None:
        """Create the collection if needed. Raises ``VectorStoreError`` if it exists with another dimension."""

    def collection_info(self, collection: str) -> dict[str, Any] | None:
        """``{"dimension": int, "embedding_model": str}`` or ``None`` when the collection does not exist."""

    def upsert(self, collection: str, records: Sequence[VectorRecord]) -> None: ...

    def query(
        self, collection: str, embedding: Sequence[float], top_k: int, where: Filter | None = None
    ) -> list[SearchHit]: ...

    def delete(self, collection: str, where: Filter) -> None:
        """Delete matching records. An empty filter is refused (it would wipe the collection)."""

    def count(self, collection: str, where: Filter | None = None) -> int: ...

    def list_collections(self) -> list[str]: ...

    def drop_collection(self, collection: str) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- shared helpers


def validate_filter(where: Filter | None, *, allow_empty: bool = True) -> dict[str, Any]:
    """Return a normalised copy of ``where`` or raise ``ValueError`` for unsupported shapes."""
    if not where:
        if allow_empty:
            return {}
        raise ValueError("A non-empty filter is required.")
    normalised: dict[str, Any] = {}
    for key, condition in where.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError(f"Invalid filter field name: {key!r}")
        if isinstance(condition, Mapping):
            if set(condition) != {"in"} or not isinstance(condition["in"], (list, tuple, set)):
                raise ValueError(f"Unsupported filter condition for '{key}'. Use a value or {{'in': [...]}}.")
            values = list(condition["in"])
            if not all(isinstance(v, (str, int, float, bool)) for v in values):
                raise ValueError(f"Filter values for '{key}' must be strings, numbers or booleans.")
            normalised[key] = {"in": values}
        elif isinstance(condition, (str, int, float, bool)):
            normalised[key] = condition
        else:
            raise ValueError(f"Filter value for '{key}' must be a string, number or boolean.")
    return normalised


def matches_nothing(where: Mapping[str, Any]) -> bool:
    """True when the filter contains an empty ``in`` list (no record can satisfy it)."""
    return any(isinstance(c, Mapping) and not c["in"] for c in where.values())


def clean_metadata(metadata: Mapping[str, Any]) -> dict[str, Scalar]:
    """Flatten metadata to what every backend supports: scalar values, identifier-like keys, no nulls."""
    cleaned: dict[str, Scalar] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError(f"Invalid metadata key: {key!r}")
        if value is None:
            continue
        cleaned[key] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return cleaned
