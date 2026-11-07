"""
Document ingestion service: versioned indexing, duplicate detection, delete, re-index and recovery.

How a new version becomes searchable (no downtime, no half-indexed state)
1. A ``processing`` version row is committed (visible in listings, never searched).
2. Chunks are embedded and written to the vector index under that version's id.
3. ONE database transaction inserts the chunk rows, marks the version ``ready``, flips the document's
   ``active_version_id`` to it and marks the previous version ``superseded``.
   Searches only ever look at vectors belonging to *active* versions, so this commit is the instant
   the new content replaces the old.
4. The previous version's vectors are deleted afterwards. If that cleanup fails, its chunk rows remain
   as a marker and the cleanup is retried at the next start-up; the stale vectors are never returned
   in the meantime because their version is no longer active.
If anything fails before step 3, the new version is marked ``failed``, its vectors are removed and the
previous version keeps answering.

The class is synchronous (it runs in worker threads); API handlers call it through ``anyio.to_thread``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.core.security import InvalidFilenameError, validate_filename
from app.db.models import Chunk, Document, DocumentVersion, iso, utcnow
from app.db.session import Database
from app.embeddings.base import EmbeddingProvider
from app.ingestion.chunking import ChunkingConfig, chunk_page
from app.ingestion.errors import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DuplicateDocumentError,
    IngestionFailedError,
    InvalidDocumentError,
)
from app.ingestion.file_store import FileStore
from app.ingestion.loaders import SUPPORTED_EXTENSIONS, PageText, load_document
from app.ingestion.types import (
    ActiveChunk,
    DocumentInfo,
    IndexReport,
    IngestResult,
    ReindexSummary,
    VersionInfo,
)
from app.vectorstore.base import VectorRecord, VectorStore

logger = logging.getLogger("app.ingestion")

MAX_COLLECTION_NAME = 63
_OUTCOMES = {"upload": "created", "update": "updated", "reindex": "reindexed"}


def collection_name(model_id: str, dimension: int) -> str:
    """Vector collection for one embedding model: vectors of different models/dimensions never mix."""
    slug = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-") or "model"
    name = f"chunks_{slug}_{dimension}"
    if len(name) > MAX_COLLECTION_NAME:
        digest = hashlib.sha256(model_id.encode()).hexdigest()[:8]
        suffix = f"-{digest}_{dimension}"
        room = MAX_COLLECTION_NAME - len("chunks_") - len(suffix)
        name = f"chunks_{slug[:room].rstrip('-')}{suffix}"
    return name


class IngestionService:
    def __init__(
        self,
        db: Database,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        file_store: FileStore,
        chunking: ChunkingConfig | None = None,
        *,
        max_upload_bytes: int = 5 * 1024 * 1024,
        keep_superseded_chunks: bool = False,
        max_parallel_embeddings: int = 2,
    ) -> None:
        self._db = db
        self._store = vector_store
        self._embedder = embedder
        self._files = file_store
        self._chunking = chunking or ChunkingConfig()
        self._max_bytes = max_upload_bytes
        self._keep_superseded = keep_superseded_chunks
        self._embed_slots = threading.Semaphore(max_parallel_embeddings)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._collection: str | None = None
        self.ready = False

    # ---- lifecycle -------------------------------------------------------------

    @property
    def collection(self) -> str:
        if self._collection is None:
            self._collection = collection_name(self._embedder.model_id, self._embedder.dimension)
        return self._collection

    @property
    def embedding_model(self) -> str:
        return self._embedder.model_id

    def start(self) -> None:
        self._db.create_all()
        self._store.ensure_collection(
            self.collection, dimension=self._embedder.dimension, embedding_model=self._embedder.model_id
        )
        self._recover()
        self.ready = True
        logger.info("ingestion_ready collection=%s store=%s", self.collection, self._store.name)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # ---- validation helpers ------------------------------------------------------

    def _validate_upload(self, name: str, data: bytes) -> tuple[str, str]:
        try:
            clean = validate_filename(name or "", SUPPORTED_EXTENSIONS)
        except InvalidFilenameError as exc:
            raise InvalidDocumentError(str(exc)) from exc
        if len(data) == 0:
            raise InvalidDocumentError("Uploaded file is empty.")
        if len(data) > self._max_bytes:
            raise DocumentTooLargeError(
                f"File is too large. Maximum size is {self._max_bytes // (1024 * 1024)} MB."
            )
        return clean, Path(clean).suffix.lower()

    def _make_chunks(self, pages: list[PageText]) -> list[tuple[int, str, int | None]]:
        chunks: list[tuple[int, str, int | None]] = []
        for page in pages:
            for text in chunk_page(page.text, self._chunking, kind=page.kind):
                chunks.append((len(chunks), text, page.page))
        if not chunks:
            raise InvalidDocumentError("No indexable text was found in this document.")
        return chunks

    # ---- ingest -----------------------------------------------------------------

    def ingest(self, name: str, data: bytes, *, source_type: str = "upload") -> IngestResult:
        """
        Add a document or a new version of an existing one.

        * same name and identical content as the active version -> ``unchanged`` (nothing is written),
        * identical content already stored under ANOTHER name -> ``DuplicateDocumentError`` (409),
        * otherwise a new version is indexed and activated.
        """
        clean, extension = self._validate_upload(name, data)
        pages = load_document(data, extension)            # rejects bad encoding / binary content early
        chunks = self._make_chunks(pages)                 # rejects documents with no indexable text early
        content_hash = hashlib.sha256(data).hexdigest()
        key = clean.lower()

        with self._lock_for(key):
            with self._db.session() as session:
                document = session.scalar(select(Document).where(Document.name_key == key))
                active = session.get(DocumentVersion, document.active_version_id) if document and document.active_version_id else None

                if active is not None and active.content_hash == content_hash and active.collection == self.collection:
                    return IngestResult("unchanged", self._document_info(document), self._version_info(active, True))

                clash = session.scalar(
                    select(Document)
                    .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
                    .where(DocumentVersion.content_hash == content_hash, Document.name_key != key)
                )
                if clash is not None:
                    raise DuplicateDocumentError(
                        f"Identical content is already stored as '{clash.name}'.",
                        details={"existing_document_id": clash.id, "existing_name": clash.name},
                    )

                if document is None:
                    reason = "upload"
                elif active is not None and active.content_hash == content_hash:
                    reason = "reindex"   # same content, but indexed with a different embedding model
                else:
                    reason = "update"

            return self._build_version(clean, key, data, chunks, content_hash, reason, source_type)

    def _build_version(
        self,
        name: str,
        key: str,
        data: bytes,
        chunks: list[tuple[int, str, int | None]],
        content_hash: str,
        reason: str,
        source_type: str,
    ) -> IngestResult:
        collection = self.collection
        extension = Path(name).suffix.lower()

        with self._db.session() as session:
            document = session.scalar(select(Document).where(Document.name_key == key))
            if document is None:
                document = Document(name=name, name_key=key, source_type=source_type)
                session.add(document)
                session.flush()
            number = (
                session.scalar(
                    select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == document.id)
                )
                or 0
            ) + 1
            version = DocumentVersion(
                document_id=document.id,
                version_number=number,
                status="processing",
                reason=reason,
                content_hash=content_hash,
                byte_size=len(data),
                file_extension=extension,
                embedding_provider=self._embedder.name,
                embedding_model=self._embedder.model_id,
                embedding_dimension=self._embedder.dimension,
                collection=collection,
                chunk_size=self._chunking.chunk_size,
                chunk_overlap=self._chunking.chunk_overlap,
                chunk_strategy=self._chunking.strategy,
            )
            session.add(version)
            session.flush()
            document_id, version_id = document.id, version.id

        try:
            stored_path = self._files.save(document_id, number, name, data)
            with self._embed_slots:
                vectors = self._embedder.embed_documents([text for _, text, _ in chunks])
            self._store.upsert(
                collection,
                [
                    VectorRecord(
                        id=f"{version_id}:{ordinal}",
                        embedding=vector,
                        text=text,
                        metadata={
                            "document_id": document_id,
                            "version_id": version_id,
                            "version": number,
                            "ordinal": ordinal,
                            "source": name,
                            "page": page,
                        },
                    )
                    for (ordinal, text, page), vector in zip(chunks, vectors)
                ],
            )
            previous = self._activate(document_id, version_id, stored_path, chunks)
        except Exception as exc:   # noqa: BLE001 - every failure must leave a clean, visible failed version
            self._fail_version(document_id, version_id, number, collection, exc)
            if isinstance(exc, AppError):
                raise
            logger.exception("ingest_failed document=%s version=%s", document_id, number)
            raise IngestionFailedError("Indexing failed. See the server logs for details.") from exc

        if previous is not None:
            self._retire_version(*previous)

        with self._db.session() as session:
            document = session.scalar(
                select(Document).options(selectinload(Document.versions)).where(Document.id == document_id)
            )
            created = next(v for v in document.versions if v.id == version_id)
            logger.info(
                "ingest_done document=%s version=%d reason=%s chunks=%d", document_id, number, reason, len(chunks)
            )
            return IngestResult(_OUTCOMES[reason], self._document_info(document), self._version_info(created, True))

    def _activate(
        self, document_id: str, version_id: str, stored_path: str, chunks: list[tuple[int, str, int | None]]
    ) -> tuple[str, str] | None:
        """Single transaction: chunk rows + ready + active pointer + previous superseded."""
        with self._db.session() as session:
            document = session.get(Document, document_id)
            version = session.get(DocumentVersion, version_id)
            previous_id = document.active_version_id

            session.add_all(
                Chunk(
                    version_id=version_id,
                    ordinal=ordinal,
                    vector_id=f"{version_id}:{ordinal}",
                    text=text,
                    char_count=len(text),
                    page=page,
                )
                for ordinal, text, page in chunks
            )
            version.stored_path = stored_path
            version.chunk_count = len(chunks)
            version.status = "ready"
            version.error = None

            previous: tuple[str, str] | None = None
            if previous_id and previous_id != version_id:
                old = session.get(DocumentVersion, previous_id)
                if old is not None:
                    old.status = "superseded"
                    previous = (old.id, old.collection)
            document.active_version_id = version_id
            document.updated_at = utcnow()
        return previous

    def _retire_version(self, version_id: str, collection: str) -> None:
        """Remove a no-longer-active version's vectors, then its chunk rows (rows left = cleanup pending)."""
        try:
            self._store.delete(collection, {"version_id": version_id})
        except Exception:   # noqa: BLE001
            logger.warning("retire_vectors_failed version=%s (will retry at next start)", version_id)
            return
        if not self._keep_superseded:
            with self._db.session() as session:
                session.execute(delete(Chunk).where(Chunk.version_id == version_id))

    def _fail_version(self, document_id: str, version_id: str, number: int, collection: str, exc: Exception) -> None:
        try:
            self._store.delete(collection, {"version_id": version_id})
        except Exception:   # noqa: BLE001
            logger.warning("fail_cleanup_vectors_failed version=%s", version_id)
        message = exc.message if isinstance(exc, AppError) else "Indexing failed."
        try:
            with self._db.session() as session:
                version = session.get(DocumentVersion, version_id)
                if version is not None:
                    version.status = "failed"
                    version.error = message[:500]
        except Exception:   # noqa: BLE001
            logger.exception("fail_mark_failed version=%s", version_id)
        try:
            self._files.remove_version(document_id, number)
        except Exception:   # noqa: BLE001
            logger.warning("fail_cleanup_files version=%s", version_id)

    # ---- delete ---------------------------------------------------------------

    def delete(self, document_id: str) -> None:
        with self._db.session() as session:
            document = session.get(Document, document_id)
            if document is None:
                raise DocumentNotFoundError("Document not found.")
            key = document.name_key
        with self._lock_for(key):
            with self._db.session() as session:
                document = session.scalar(
                    select(Document).options(selectinload(Document.versions)).where(Document.id == document_id)
                )
                if document is None:
                    raise DocumentNotFoundError("Document not found.")
                collections = {v.collection for v in document.versions if v.collection}
                session.delete(document)   # database first: from this commit on, searches cannot return it
            for collection in collections:
                try:
                    self._store.delete(collection, {"document_id": document_id})
                except Exception:   # noqa: BLE001 - the collection may be gone; vectors are unreachable anyway
                    logger.warning("delete_vectors_failed document=%s collection=%s", document_id, collection)
            self._files.remove_document(document_id)
        logger.info("document_deleted document=%s", document_id)

    def delete_by_name(self, name: str) -> None:
        document = self.get_by_name(name)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        self.delete(document.id)

    # ---- re-index ---------------------------------------------------------------

    def reindex_document(self, document_id: str) -> IngestResult:
        """Rebuild a document's index from its stored original with the CURRENT embedding model and chunking."""
        with self._db.session() as session:
            document = session.scalar(
                select(Document).options(selectinload(Document.versions)).where(Document.id == document_id)
            )
            if document is None:
                raise DocumentNotFoundError("Document not found.")
            name, key = document.name, document.name_key
            source = next(
                (v for v in reversed(document.versions) if v.stored_path and v.status in ("ready", "superseded")),
                None,
            )
            if source is None:
                raise InvalidDocumentError("The original file of this document is not available to re-index.")
            stored_path, content_hash, source_type = source.stored_path, source.content_hash, document.source_type

        with self._lock_for(key):
            try:
                data = self._files.read(stored_path)
            except (OSError, InvalidFilenameError) as exc:
                raise InvalidDocumentError("The original file of this document is missing on disk.") from exc
            pages = load_document(data, Path(name).suffix)
            chunks = self._make_chunks(pages)
            return self._build_version(name, key, data, chunks, content_hash, "reindex", source_type)

    def reindex_all(self) -> ReindexSummary:
        documents = self.list_documents()
        failed: list[dict] = []
        succeeded = 0
        for document in documents:
            try:
                self.reindex_document(document.id)
                succeeded += 1
            except AppError as exc:
                failed.append({"document_id": document.id, "name": document.name, "error": exc.message})
        return ReindexSummary(total=len(documents), succeeded=succeeded, failed=failed)

    # ---- queries -----------------------------------------------------------------

    def _version_info(self, version: DocumentVersion, is_active: bool) -> VersionInfo:
        return VersionInfo(
            id=version.id,
            version_number=version.version_number,
            status=version.status,
            reason=version.reason,
            content_hash=version.content_hash,
            byte_size=version.byte_size,
            file_extension=version.file_extension,
            chunk_count=version.chunk_count,
            embedding_provider=version.embedding_provider,
            embedding_model=version.embedding_model,
            embedding_dimension=version.embedding_dimension,
            collection=version.collection,
            chunk_size=version.chunk_size,
            chunk_overlap=version.chunk_overlap,
            chunk_strategy=version.chunk_strategy or "sentence",
            error=version.error,
            created_at=iso(version.created_at),
            is_active=is_active,
        )

    def _document_info(self, document: Document) -> DocumentInfo:
        versions = sorted(document.versions, key=lambda v: v.version_number)
        active = next((v for v in versions if v.id == document.active_version_id), None)
        latest = versions[-1] if versions else None
        if active is not None:
            status = "ready"
            stale = active.collection != self.collection
            outdated = (active.chunk_size, active.chunk_overlap, active.chunk_strategy or "sentence") != (
                self._chunking.chunk_size, self._chunking.chunk_overlap, self._chunking.strategy
            )
            newer_failure = latest is not None and latest.version_number > active.version_number and latest.status == "failed"
            last_error = latest.error if newer_failure else None
        else:
            status = "processing" if latest is not None and latest.status == "processing" else "failed"
            stale = False
            outdated = False
            last_error = latest.error if latest is not None else None
        return DocumentInfo(
            id=document.id,
            name=document.name,
            source_type=document.source_type,
            created_at=iso(document.created_at),
            updated_at=iso(document.updated_at),
            status=status,
            stale=stale,
            chunking_outdated=outdated,
            version_count=len(versions),
            chunk_count=active.chunk_count if active else 0,
            active_version=self._version_info(active, True) if active else None,
            last_error=last_error,
        )

    def list_documents(self) -> list[DocumentInfo]:
        with self._db.session() as session:
            rows = session.scalars(
                select(Document).options(selectinload(Document.versions)).order_by(Document.name_key)
            ).all()
            return [self._document_info(d) for d in rows]

    def get_document(self, document_id: str) -> DocumentInfo:
        with self._db.session() as session:
            document = session.scalar(
                select(Document).options(selectinload(Document.versions)).where(Document.id == document_id)
            )
            if document is None:
                raise DocumentNotFoundError("Document not found.")
            return self._document_info(document)

    def get_by_name(self, name: str) -> DocumentInfo | None:
        with self._db.session() as session:
            document = session.scalar(
                select(Document).options(selectinload(Document.versions)).where(Document.name_key == name.lower())
            )
            return self._document_info(document) if document else None

    def list_versions(self, document_id: str) -> list[VersionInfo]:
        with self._db.session() as session:
            document = session.scalar(
                select(Document).options(selectinload(Document.versions)).where(Document.id == document_id)
            )
            if document is None:
                raise DocumentNotFoundError("Document not found.")
            versions = sorted(document.versions, key=lambda v: v.version_number, reverse=True)
            return [self._version_info(v, v.id == document.active_version_id) for v in versions]

    def active_version_ids(self) -> list[str]:
        """Versions that answers may use: active, ready and indexed with the CURRENT embedding model."""
        with self._db.session() as session:
            return list(
                session.scalars(
                    select(DocumentVersion.id)
                    .join(Document, Document.active_version_id == DocumentVersion.id)
                    .where(DocumentVersion.status == "ready", DocumentVersion.collection == self.collection)
                )
            )

    def active_chunks(self) -> list[ActiveChunk]:
        """All passages answers may use (same visibility rule as ``active_version_ids``)."""
        with self._db.session() as session:
            rows = session.execute(
                select(
                    Chunk.vector_id, Chunk.text, Document.name, Document.id,
                    DocumentVersion.version_number, DocumentVersion.id, Chunk.ordinal, Chunk.page,
                )
                .join(DocumentVersion, Chunk.version_id == DocumentVersion.id)
                .join(Document, Document.active_version_id == DocumentVersion.id)
                .where(DocumentVersion.status == "ready", DocumentVersion.collection == self.collection)
                .order_by(Document.name, Chunk.ordinal)
            ).all()
        return [ActiveChunk(*row) for row in rows]

    def index_report(self) -> IndexReport:
        """Compare what the database says should be searchable with what the vector index holds."""
        ids = self.active_version_ids()
        with self._db.session() as session:
            documents = session.scalar(select(func.count()).select_from(Document)) or 0
            ready = session.scalar(
                select(func.count()).select_from(Document).where(Document.active_version_id.is_not(None))
            ) or 0
            expected = session.scalar(
                select(func.coalesce(func.sum(DocumentVersion.chunk_count), 0)).where(DocumentVersion.id.in_(ids))
            ) if ids else 0
            processing = session.scalar(
                select(func.count()).select_from(DocumentVersion).where(DocumentVersion.status == "processing")
            ) or 0
        indexed = self._store.count(self.collection, {"version_id": {"in": ids}}) if ids else 0
        total = self._store.count(self.collection)
        current_ready = len(ids)
        return IndexReport(
            collection=self.collection,
            embedding_model=self._embedder.model_id,
            vector_store=self._store.name,
            documents=documents,
            ready_documents=ready,
            stale_documents=max(0, ready - current_ready),
            processing_versions=processing,
            expected_vectors=int(expected or 0),
            indexed_vectors=indexed,
            total_vectors=total,
            orphan_vectors=max(0, total - indexed),
            consistent=int(expected or 0) == indexed,
        )

    # ---- start-up recovery and seeding ----------------------------------------------

    def _recover(self) -> None:
        """Fail uploads interrupted by a crash and retry cleanups that did not finish."""
        with self._db.session() as session:
            interrupted = [
                (v.id, v.document_id, v.version_number, v.collection)
                for v in session.scalars(select(DocumentVersion).where(DocumentVersion.status == "processing"))
            ]
        for version_id, document_id, number, collection in interrupted:
            self._fail_version(
                document_id, version_id, number, collection,
                AppError("Interrupted by a restart; upload the file again or re-index the document."),
            )
        if interrupted:
            logger.warning("recovered_interrupted_versions count=%d", len(interrupted))

        with self._db.session() as session:
            pending = list(
                session.execute(
                    select(DocumentVersion.id, DocumentVersion.collection).where(
                        DocumentVersion.status != "ready",
                        select(Chunk.id).where(Chunk.version_id == DocumentVersion.id).exists(),
                    )
                ).all()
            )
        for version_id, collection in pending:
            self._retire_version(version_id, collection)

    def seed_from_directory(self, directory: Path) -> int:
        """Ingest the supported files of ``directory`` once, and only when the database holds no documents."""
        with self._db.session() as session:
            if (session.scalar(select(func.count()).select_from(Document)) or 0) > 0:
                return 0
        if not directory.is_dir():
            return 0
        added = 0
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                self.ingest(path.name, path.read_bytes(), source_type="seed")
                added += 1
            except AppError as exc:
                logger.warning("seed_skipped file=%s reason=%s", path.name, exc.message)
        logger.info("seeded documents=%d from=%s", added, directory)
        return added
