"""
Tests for the ingestion service: versioning, duplicate detection, atomic activation, delete, re-index,
failure handling, embedding-model change, recovery and seeding.

Real SQLite + real ChromaDB in a temp directory; a deterministic fake embedder (no model download).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import Chunk, Document, DocumentVersion
from app.db.session import Database
from app.embeddings.base import EmbeddingError
from app.ingestion.chunking import ChunkingConfig
from app.ingestion.errors import (
    DocumentNotFoundError,
    DocumentTooLargeError,
    DuplicateDocumentError,
    InvalidDocumentError,
)
from app.ingestion.file_store import FileStore
from app.ingestion.service import IngestionService, collection_name
from app.rag.retrieval import KnowledgeBaseNotReadyError
from app.rag.store_retriever import StoreRetriever
from app.vectorstore.base import VectorRecord, VectorStoreError
from app.vectorstore.chroma_store import ChromaVectorStore
from tests.embedders import HashingEmbedder

pytestmark = pytest.mark.anyio

PL_V1 = (
    "# Profit and Loss\n\n"
    "In 2020, the company reported revenue of $11.8 million and a net loss of $2.8 million. "
    "The loss was caused by pandemic disruption.\n\n"
    "In 2021 the company reported a profit of $1.4 million after a strong recovery."
)
PL_V2 = (
    "# Profit and Loss\n\n"
    "In 2020, the company reported revenue of $11.8 million and a net loss of $2.8 million. "
    "The loss was caused by pandemic disruption.\n\n"
    "In 2024 the company reported a net loss of $1.2 million due to higher hiring costs."
)
IVR = (
    "# IVR Testing\n\n## Invalid DTMF\n\n"
    "After three consecutive invalid inputs, the IVR should route the caller to the configured fallback destination."
)
SIP = (
    "# SIP Testing\n\n## SIP Registration\n\n"
    "A SIP endpoint should successfully register with the SIP server using valid credentials."
)


def utf8(text: str) -> bytes:
    return text.encode("utf-8")


def build_kb(tmp_path, *, chunking=None, max_upload_bytes=10_000, embedder=None):
    db = Database(f"sqlite:///{(tmp_path / 'kb.db').as_posix()}")
    store = ChromaVectorStore(tmp_path / "chroma")
    embedder = embedder or HashingEmbedder()
    files = FileStore(tmp_path / "uploads")
    service = IngestionService(db, store, embedder, files, chunking or ChunkingConfig(300, 50), max_upload_bytes=max_upload_bytes)
    service.start()
    return SimpleNamespace(
        db=db, store=store, embedder=embedder, files=files, service=service,
        retriever=StoreRetriever(service, store, embedder), tmp=tmp_path,
    )


@pytest.fixture
def kb(tmp_path):
    return build_kb(tmp_path)


def rows(kb, model, *conditions):
    with kb.db.session() as session:
        return session.scalars(select(model).where(*conditions)).all()


# --------------------------------------------------------------------------- ingest basics


def test_first_upload_creates_a_searchable_document(kb):
    result = kb.service.ingest("profit_and_loss.txt", utf8(PL_V1))

    assert result.outcome == "created"
    document, version = result.document, result.version
    assert (document.status, document.version_count, document.stale) == ("ready", 1, False)
    assert (version.version_number, version.status, version.reason, version.is_active) == (1, "ready", "upload", True)
    assert version.chunk_count == document.chunk_count > 0
    assert version.embedding_model == "fake-bow-64" and version.collection == collection_name("fake-bow-64", 64)

    assert len(rows(kb, Chunk)) == version.chunk_count                       # database chunk rows
    assert kb.store.count(kb.service.collection) == version.chunk_count      # vectors
    assert (kb.tmp / "uploads" / document.id / "v1" / "profit_and_loss.txt").read_bytes() == utf8(PL_V1)
    report = kb.service.index_report()
    assert report.consistent and report.orphan_vectors == 0 and report.ready_documents == 1


async def test_search_returns_stable_ids_and_document_metadata(kb):
    result = kb.service.ingest("profit_and_loss.txt", utf8(PL_V1))
    kb.service.ingest("ivr_testing.txt", utf8(IVR))

    hits = await kb.retriever.retrieve("net loss 2020 pandemic disruption", 3)

    top = hits[0]
    assert top.source == "profit_and_loss.txt" and top.document_id == result.document.id and top.version == 1
    assert isinstance(top.chunk_index, int) and top.score > 0.3
    assert top.chunk_id == f"profit_and_loss.txt#{top.chunk_index}"
    assert {h.source for h in hits} <= {"profit_and_loss.txt", "ivr_testing.txt"}


def test_identical_reupload_is_a_no_op(kb):
    first = kb.service.ingest("a.txt", utf8(PL_V1))
    calls = kb.embedder.document_calls

    again = kb.service.ingest("a.txt", utf8(PL_V1))

    assert again.outcome == "unchanged" and again.document.id == first.document.id
    assert again.document.version_count == 1 and kb.embedder.document_calls == calls


def test_names_are_case_insensitive(kb):
    first = kb.service.ingest("Notes.TXT", utf8(PL_V1))
    second = kb.service.ingest("notes.txt", utf8(PL_V2))
    assert second.document.id == first.document.id and second.outcome == "updated"
    assert len(kb.service.list_documents()) == 1


# --------------------------------------------------------------------------- versioning


async def test_new_content_creates_a_new_version_that_replaces_the_old(kb):
    v1 = kb.service.ingest("pl.txt", utf8(PL_V1))
    v2 = kb.service.ingest("pl.txt", utf8(PL_V2))

    assert v2.outcome == "updated" and v2.version.version_number == 2 and v2.version.reason == "update"
    assert v2.document.id == v1.document.id and v2.document.version_count == 2

    versions = kb.service.list_versions(v1.document.id)
    assert [(v.version_number, v.status, v.is_active) for v in versions] == [(2, "ready", True), (1, "superseded", False)]

    # Only the new version is searchable and stored: old vectors and chunk rows are cleaned up.
    assert kb.store.count(kb.service.collection) == v2.version.chunk_count
    assert {c.version_id for c in rows(kb, Chunk)} == {v2.version.id}
    hits = await kb.retriever.retrieve("net loss 2024 hiring costs", 5)
    assert all(h.version == 2 for h in hits) and "hiring" in " ".join(h.text for h in hits)
    assert not any("strong recovery" in h.text for h in hits)

    # The original file of the superseded version is kept for audit and rollback.
    assert (kb.tmp / "uploads" / v1.document.id / "v1" / "pl.txt").exists()
    assert kb.service.index_report().consistent


def test_failed_cleanup_of_the_old_version_is_retried_at_startup(kb, monkeypatch):
    kb.service.ingest("pl.txt", utf8(PL_V1))
    real_delete = kb.store.delete
    monkeypatch.setattr(kb.store, "delete", lambda *a, **k: (_ for _ in ()).throw(VectorStoreError("boom")))
    kb.service.ingest("pl.txt", utf8(PL_V2))                 # activation works; cleanup of v1 fails quietly
    monkeypatch.setattr(kb.store, "delete", real_delete)

    report = kb.service.index_report()
    assert report.orphan_vectors > 0 and report.consistent   # stale vectors exist but are not searchable
    assert len(rows(kb, Chunk, Chunk.version_id != kb.service.list_versions(kb.service.list_documents()[0].id)[0].id)) > 0

    kb.service.start()                                        # restart: pending cleanup is retried
    assert kb.service.index_report().orphan_vectors == 0
    assert len(rows(kb, DocumentVersion, DocumentVersion.status == "superseded")) == 1
    assert len(rows(kb, Chunk)) == kb.service.list_documents()[0].chunk_count


# --------------------------------------------------------------------------- duplicates and validation


def test_same_content_under_another_name_is_rejected_with_a_pointer_to_the_original(kb):
    original = kb.service.ingest("original.txt", utf8(IVR))

    with pytest.raises(DuplicateDocumentError) as info:
        kb.service.ingest("copy.txt", utf8(IVR))

    assert info.value.status_code == 409
    assert info.value.details == {"existing_document_id": original.document.id, "existing_name": "original.txt"}
    assert [d.name for d in kb.service.list_documents()] == ["original.txt"]


def test_content_can_be_reused_after_the_original_is_deleted(kb):
    original = kb.service.ingest("original.txt", utf8(IVR))
    kb.service.delete(original.document.id)
    assert kb.service.ingest("copy.txt", utf8(IVR)).outcome == "created"


@pytest.mark.parametrize(
    "name, data, error",
    [
        ("..\\evil.txt", b"hello hello hello hello hello hello", InvalidDocumentError),
        ("../evil.txt", b"hello hello hello hello hello hello", InvalidDocumentError),
        ("evil.exe", b"hello hello hello hello hello hello", InvalidDocumentError),
        ("empty.txt", b"", InvalidDocumentError),
        ("latin.txt", "caf\xe9 caf\xe9 caf\xe9 caf\xe9 caf\xe9 caf\xe9".encode("latin-1"), InvalidDocumentError),
        ("binary.txt", b"abc\x00def " * 10, InvalidDocumentError),
        ("blank.txt", b"   \n\n   ", InvalidDocumentError),
        ("tiny.txt", b"too short", InvalidDocumentError),            # yields no indexable chunk
        ("big.txt", b"x " * 6000, DocumentTooLargeError),
    ],
)
def test_invalid_uploads_are_rejected_without_leaving_traces(kb, name, data, error):
    with pytest.raises(error):
        kb.service.ingest(name, data)
    assert kb.service.list_documents() == []
    assert kb.store.count(kb.service.collection) == 0
    assert list((kb.tmp / "uploads").iterdir()) == []


def test_utf8_bom_is_accepted(kb):
    result = kb.service.ingest("bom.txt", b"\xef\xbb\xbf" + utf8(IVR))
    assert result.outcome == "created"


def test_markdown_files_are_supported(kb):
    assert kb.service.ingest("readme.md", utf8(SIP)).document.name == "readme.md"


# --------------------------------------------------------------------------- delete


async def test_delete_removes_database_vectors_files_and_search_results(kb):
    keep = kb.service.ingest("keep.txt", utf8(SIP))
    gone = kb.service.ingest("gone.txt", utf8(IVR))

    kb.service.delete(gone.document.id)

    assert [d.name for d in kb.service.list_documents()] == ["keep.txt"]
    assert kb.store.count(kb.service.collection) == keep.version.chunk_count
    assert not (kb.tmp / "uploads" / gone.document.id).exists()
    assert rows(kb, DocumentVersion, DocumentVersion.document_id == gone.document.id) == []
    hits = await kb.retriever.retrieve("three consecutive invalid inputs fallback destination", 5)
    assert all(h.source == "keep.txt" for h in hits)
    assert kb.service.index_report().consistent


def test_delete_unknown_document_and_delete_by_name(kb):
    with pytest.raises(DocumentNotFoundError):
        kb.service.delete("0" * 32)
    with pytest.raises(DocumentNotFoundError):
        kb.service.delete_by_name("nothing.txt")

    kb.service.ingest("Some.txt", utf8(SIP))
    kb.service.delete_by_name("some.TXT")
    assert kb.service.list_documents() == []


def test_a_deleted_name_starts_again_at_version_one(kb):
    first = kb.service.ingest("a.txt", utf8(SIP))
    kb.service.delete(first.document.id)
    again = kb.service.ingest("a.txt", utf8(SIP))
    assert again.version.version_number == 1 and again.document.id != first.document.id


# --------------------------------------------------------------------------- failures


def test_failed_first_upload_is_visible_and_leaves_nothing_searchable(kb):
    kb.embedder.fail_next_documents = True
    with pytest.raises(EmbeddingError):
        kb.service.ingest("a.txt", utf8(SIP))

    (document,) = kb.service.list_documents()
    assert document.status == "failed" and "simulated" in document.last_error and document.active_version is None
    assert kb.store.count(kb.service.collection) == 0
    assert not (kb.tmp / "uploads" / document.id / "v1").exists()
    assert kb.service.active_version_ids() == []

    recovered = kb.service.ingest("a.txt", utf8(SIP))                # the same name can simply be uploaded again
    assert recovered.version.version_number == 2 and recovered.document.status == "ready"
    assert recovered.document.last_error is None


async def test_failed_update_keeps_the_previous_version_answering(kb):
    kb.service.ingest("pl.txt", utf8(PL_V1))
    kb.embedder.fail_next_documents = True
    with pytest.raises(EmbeddingError):
        kb.service.ingest("pl.txt", utf8(PL_V2))

    (document,) = kb.service.list_documents()
    assert document.status == "ready" and document.active_version.version_number == 1
    assert "simulated" in document.last_error
    versions = {v.version_number: v.status for v in kb.service.list_versions(document.id)}
    assert versions == {2: "failed", 1: "ready"}
    hits = await kb.retriever.retrieve("strong recovery profit 2021", 3)
    assert hits and all(h.version == 1 for h in hits)
    assert kb.service.index_report().consistent


def test_failure_while_storing_vectors_is_rolled_back_cleanly(kb, monkeypatch):
    kb.service.ingest("pl.txt", utf8(PL_V1))
    before = kb.store.count(kb.service.collection)
    monkeypatch.setattr(kb.store, "upsert", lambda *a, **k: (_ for _ in ()).throw(VectorStoreError("disk full")))

    with pytest.raises(VectorStoreError):
        kb.service.ingest("pl.txt", utf8(PL_V2))

    assert kb.store.count(kb.service.collection) == before
    assert kb.service.get_by_name("pl.txt").active_version.version_number == 1


def test_failure_during_activation_removes_the_new_vectors(kb, monkeypatch):
    kb.service.ingest("pl.txt", utf8(PL_V1))
    before = kb.store.count(kb.service.collection)
    monkeypatch.setattr(kb.service, "_activate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(AppError) as info:
        kb.service.ingest("pl.txt", utf8(PL_V2))

    assert info.value.code == "ingestion_failed" and "db down" not in info.value.message   # no internals leak
    assert kb.store.count(kb.service.collection) == before
    assert kb.service.get_by_name("pl.txt").active_version.version_number == 1


# --------------------------------------------------------------------------- re-index


def test_reindex_rebuilds_from_the_stored_original_with_current_chunking(kb):
    created = kb.service.ingest("pl.txt", utf8(PL_V1))
    kb.service._chunking = ChunkingConfig(chunk_size=100, chunk_overlap=20)   # simulate a configuration change

    result = kb.service.reindex_document(created.document.id)

    assert result.outcome == "reindexed" and result.version.reason == "reindex"
    assert result.version.version_number == 2 and result.version.content_hash == created.version.content_hash
    assert result.version.chunk_size == 100 and result.version.chunk_count > created.version.chunk_count
    assert kb.store.count(kb.service.collection) == result.version.chunk_count
    assert kb.service.index_report().consistent


def test_reindex_errors(kb):
    with pytest.raises(DocumentNotFoundError):
        kb.service.reindex_document("0" * 32)
    created = kb.service.ingest("pl.txt", utf8(PL_V1))
    (kb.tmp / "uploads" / created.document.id / "v1" / "pl.txt").unlink()
    with pytest.raises(InvalidDocumentError):
        kb.service.reindex_document(created.document.id)
    assert kb.service.get_by_name("pl.txt").active_version.version_number == 1


async def test_changing_the_embedding_model_makes_documents_stale_until_reindexed(tmp_path):
    first = build_kb(tmp_path)
    first.service.ingest("pl.txt", utf8(PL_V1))
    first.service.ingest("ivr.txt", utf8(IVR))
    old_collection = first.service.collection

    # Same database, vector store and files; a different embedding model/dimension.
    other = HashingEmbedder(model_id="other-model", dimension=32)
    service = IngestionService(first.db, first.store, other, first.files, ChunkingConfig(300, 50))
    service.start()
    retriever = StoreRetriever(service, first.store, other)

    assert service.collection != old_collection
    assert {d.stale for d in service.list_documents()} == {True}
    assert service.active_version_ids() == [] and await retriever.retrieve("net loss", 3) == []
    assert service.index_report().stale_documents == 2

    summary = service.reindex_all()

    assert (summary.total, summary.succeeded, summary.failed) == (2, 2, [])
    assert {d.stale for d in service.list_documents()} == {False}
    hits = await retriever.retrieve("net loss 2020 pandemic disruption", 3)
    assert hits and hits[0].source == "pl.txt"
    assert first.store.count(old_collection) == 0              # old-model vectors were retired
    assert service.index_report().consistent


def test_reindex_all_reports_individual_failures(kb):
    ok = kb.service.ingest("ok.txt", utf8(SIP))
    broken = kb.service.ingest("broken.txt", utf8(IVR))
    (kb.tmp / "uploads" / broken.document.id / "v1" / "broken.txt").unlink()

    summary = kb.service.reindex_all()

    assert (summary.total, summary.succeeded) == (2, 1)
    assert [f["name"] for f in summary.failed] == ["broken.txt"]
    assert kb.service.get_document(ok.document.id).active_version.version_number == 2


# --------------------------------------------------------------------------- index consistency and recovery


def test_index_report_detects_orphan_vectors(kb):
    kb.service.ingest("a.txt", utf8(SIP))
    kb.store.upsert(
        kb.service.collection,
        [VectorRecord("stray:0", [0.1] * 64, "stray", {"version_id": "stray", "document_id": "x", "source": "x"})],
    )
    report = kb.service.index_report()
    assert report.orphan_vectors == 1 and report.total_vectors == report.indexed_vectors + 1


def test_startup_recovers_interrupted_uploads(kb):
    created = kb.service.ingest("a.txt", utf8(SIP))
    # Simulate a crash in the middle of a second upload: a `processing` version plus some vectors.
    with kb.db.session() as session:
        stuck = DocumentVersion(
            document_id=created.document.id, version_number=2, status="processing", reason="update",
            content_hash="f" * 64, collection=kb.service.collection,
        )
        session.add(stuck)
        session.flush()
        stuck_id = stuck.id
    kb.store.upsert(
        kb.service.collection,
        [VectorRecord(f"{stuck_id}:0", [0.2] * 64, "partial", {"version_id": stuck_id, "document_id": created.document.id})],
    )

    kb.service.start()

    version = rows(kb, DocumentVersion, DocumentVersion.id == stuck_id)[0]
    assert version.status == "failed" and "Interrupted" in version.error
    assert kb.store.count(kb.service.collection, {"version_id": stuck_id}) == 0
    assert kb.service.get_by_name("a.txt").status == "ready"


def test_data_survives_restarting_the_service_on_the_same_storage(tmp_path):
    first = build_kb(tmp_path)
    first.service.ingest("a.txt", utf8(SIP))
    first.store.close()
    first.db.dispose()

    second = build_kb(tmp_path)           # new objects over the same files
    assert [d.name for d in second.service.list_documents()] == ["a.txt"]
    assert second.service.index_report().consistent


# --------------------------------------------------------------------------- queries


def test_listing_and_lookup(kb):
    kb.service.ingest("b.txt", utf8(IVR))
    kb.service.ingest("a.txt", utf8(SIP))

    assert [d.name for d in kb.service.list_documents()] == ["a.txt", "b.txt"]
    assert kb.service.get_by_name("A.TXT").name == "a.txt"
    assert kb.service.get_by_name("missing.txt") is None
    with pytest.raises(DocumentNotFoundError):
        kb.service.get_document("0" * 32)
    with pytest.raises(DocumentNotFoundError):
        kb.service.list_versions("0" * 32)


async def test_retriever_requires_a_started_service_and_handles_an_empty_index(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    store = ChromaVectorStore(tmp_path / "chroma")
    embedder = HashingEmbedder()
    service = IngestionService(db, store, embedder, FileStore(tmp_path / "up"))
    retriever = StoreRetriever(service, store, embedder)

    with pytest.raises(KnowledgeBaseNotReadyError):
        await retriever.retrieve("anything", 3)

    service.start()
    assert await retriever.retrieve("anything", 3) == []


# --------------------------------------------------------------------------- seeding


def test_seeding_runs_once_on_an_empty_database(kb, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.txt").write_bytes(utf8(SIP))
    (seed / "b.md").write_bytes(utf8(IVR))
    (seed / "image.png").write_bytes(b"\x89PNG")
    (seed / "dup.txt").write_bytes(utf8(SIP))        # same content as a.txt -> skipped, not fatal

    assert kb.service.seed_from_directory(seed) == 2
    assert [(d.name, d.source_type) for d in kb.service.list_documents()] == [("a.txt", "seed"), ("b.md", "seed")]

    (seed / "late.txt").write_bytes(utf8(PL_V1))
    assert kb.service.seed_from_directory(seed) == 0           # database no longer empty: do nothing
    assert len(kb.service.list_documents()) == 2


def test_seeding_a_missing_directory_is_harmless(kb, tmp_path):
    assert kb.service.seed_from_directory(tmp_path / "nope") == 0


# --------------------------------------------------------------------------- concurrency


def test_parallel_uploads_of_different_documents_all_succeed_consistently(kb):
    texts = {f"doc{i}.txt": f"# Doc {i}\n\nThis is document number {i} and it talks about topic {i} in some detail. " * 3 for i in range(6)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda item: kb.service.ingest(item[0], utf8(item[1])), texts.items()))

    assert all(r.outcome == "created" for r in results)
    assert len(kb.service.list_documents()) == 6
    assert kb.service.index_report().consistent


def test_parallel_uploads_of_the_same_name_are_serialised(kb):
    barrier = threading.Barrier(2)

    def upload(content):
        barrier.wait()
        return kb.service.ingest("same.txt", utf8(content))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [PL_V1, PL_V2]))

    assert sorted(r.version.version_number for r in results) == [1, 2]
    document = kb.service.get_by_name("same.txt")
    assert document.version_count == 2 and kb.service.index_report().consistent


def test_collection_names_are_valid_and_model_specific():
    assert collection_name("all-MiniLM-L6-v2", 384) == "chunks_all-minilm-l6-v2_384"
    assert collection_name("a/b c", 8) == "chunks_a-b-c_8"
    long = collection_name("x" * 200, 1536)
    assert len(long) <= 63 and long.endswith("_1536")
    assert collection_name("x" * 200, 1536) == long and collection_name("y" * 200, 1536) != long
