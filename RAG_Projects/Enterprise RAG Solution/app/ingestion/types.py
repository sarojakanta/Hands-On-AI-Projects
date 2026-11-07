"""Plain data objects returned by the ingestion service (detached from database sessions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class ActiveChunk(NamedTuple):
    """A searchable passage of an active document version (used to build the keyword index)."""

    vector_id: str
    text: str
    source: str
    document_id: str
    version: int
    version_id: str
    ordinal: int
    page: int | None


@dataclass(frozen=True)
class VersionInfo:
    id: str
    version_number: int
    status: str                 # processing | ready | failed | superseded
    reason: str                 # upload | update | reindex
    content_hash: str
    byte_size: int
    file_extension: str
    chunk_count: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    collection: str
    chunk_size: int
    chunk_overlap: int
    chunk_strategy: str
    error: str | None
    created_at: str | None
    is_active: bool


@dataclass(frozen=True)
class DocumentInfo:
    id: str
    name: str
    source_type: str
    created_at: str | None
    updated_at: str | None
    status: str                 # ready | processing | failed
    stale: bool                 # indexed with an embedding model other than the current one
    chunking_outdated: bool     # chunked with settings other than the current ones (re-index to apply)
    version_count: int
    chunk_count: int            # chunks of the active version (0 when none)
    active_version: VersionInfo | None
    last_error: str | None      # error of the most recent failed attempt, if it is newer than the active version


@dataclass(frozen=True)
class IngestResult:
    outcome: str                # created | updated | reindexed | unchanged
    document: DocumentInfo
    version: VersionInfo


@dataclass(frozen=True)
class ReindexSummary:
    total: int
    succeeded: int
    failed: list[dict] = field(default_factory=list)   # [{"document_id", "name", "error"}]


@dataclass(frozen=True)
class IndexReport:
    collection: str
    embedding_model: str
    vector_store: str
    documents: int
    ready_documents: int
    stale_documents: int
    processing_versions: int
    expected_vectors: int       # chunks of active, current-model versions according to the database
    indexed_vectors: int        # vectors in the index that belong to those versions
    total_vectors: int          # everything in the collection
    orphan_vectors: int         # total - indexed (cleaned up by superseded/failed version cleanup)
    consistent: bool
