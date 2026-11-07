"""Tests for chunking, loaders and the file store."""

from __future__ import annotations

import pytest

from app.core.security import InvalidFilenameError
from app.ingestion.chunking import ChunkingConfig, chunk_text
from app.ingestion.errors import InvalidDocumentError
from app.ingestion.file_store import FileStore
from app.ingestion.loaders import LOADERS, SUPPORTED_EXTENSIONS, load_document

# --------------------------------------------------------------------------- chunking

TEAMS = """# Teams Calling Testing

## Outbound Calling

A provisioned Teams user should be able to make
an outbound call when the user has the required
calling configuration and phone number.

The call should transition to a connected state
when the destination answers.

## Voicemail

When the destination does not answer and voicemail
is configured, the call should be routed to voicemail
according to the applicable configuration.

## Call Transfer

A connected call should be transferable to another
valid destination when the user has the required
calling permissions.
"""


def test_defaults():
    assert ChunkingConfig() == ChunkingConfig(300, 50)


def test_chunks_never_cross_a_heading():
    chunks = chunk_text(TEAMS)
    assert len(chunks) == 3
    assert chunks[0].startswith("# Teams Calling Testing\n## Outbound Calling")
    assert chunks[1].startswith("## Voicemail") and "Call Transfer" not in chunks[1]
    assert chunks[2].startswith("## Call Transfer")


def test_heading_only_sections_are_attached_to_the_next_section():
    chunks = chunk_text("# Title\n\n## Section\n\nThis section has a body that is long enough to keep.")
    assert len(chunks) == 1 and chunks[0].startswith("# Title\n## Section")


def test_sentences_are_packed_up_to_the_chunk_size():
    text = " ".join(f"This is sentence number {i} of the document." for i in range(30))
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 3 and all(len(c) <= 200 for c in chunks)
    assert " ".join(chunks).count("sentence number") == 30      # nothing lost, nothing duplicated


def test_a_sentence_longer_than_the_chunk_size_is_split_with_overlap():
    sentence = "word " * 100 + "end."
    chunks = chunk_text(sentence, chunk_size=120, overlap=30)
    assert len(chunks) > 3 and all(len(c) <= 120 for c in chunks)
    assert chunks[0][-30:].strip() in chunks[1]                 # consecutive chunks overlap


def test_tiny_fragments_are_dropped_and_blank_lines_collapsed():
    assert chunk_text("short") == []
    assert chunk_text("   \n\n\n   ") == []
    text = "First paragraph that is long enough to survive.\n\n\n\n\nSecond paragraph that is also long enough."
    assert len(chunk_text(text)) == 1


def test_chunking_is_deterministic():
    assert chunk_text(TEAMS) == chunk_text(TEAMS)


# --------------------------------------------------------------------------- loaders


def test_supported_formats():
    assert SUPPORTED_EXTENSIONS == {".txt", ".md", ".pdf", ".docx", ".csv"} == set(LOADERS)


def test_text_is_loaded_as_a_single_page_without_page_number():
    (page,) = load_document("héllo wörld".encode("utf-8"), ".txt")
    assert page.page is None and page.text == "héllo wörld"
    assert load_document(b"# Title", ".MD")[0].text == "# Title"      # extension is case-insensitive


def test_bom_is_stripped():
    assert load_document(b"\xef\xbb\xbfhello", ".txt")[0].text == "hello"


@pytest.mark.parametrize(
    "data, extension",
    [(b"\xff\xfe\x00bad", ".txt"), (b"abc\x00def", ".txt"), (b"   \n ", ".txt"), (b"x", ".pdf"), (b"x", "")],
)
def test_unloadable_content_is_rejected(data, extension):
    with pytest.raises(InvalidDocumentError):
        load_document(data, extension)


# --------------------------------------------------------------------------- file store

DOC = "a" * 32


def test_files_round_trip_under_document_and_version_directories(tmp_path):
    store = FileStore(tmp_path / "files")
    relative = store.save(DOC, 2, "notes.txt", b"hello")

    assert relative == f"{DOC}/v2/notes.txt"
    assert (tmp_path / "files" / DOC / "v2" / "notes.txt").read_bytes() == b"hello"
    assert store.read(relative) == b"hello"


@pytest.mark.parametrize("name", ["../x.txt", "..\\x.txt", "a/b.txt", "C:\\x.txt"])
def test_hostile_file_names_cannot_escape_the_version_directory(tmp_path, name):
    store = FileStore(tmp_path / "files")
    with pytest.raises(InvalidFilenameError):
        store.save(DOC, 1, name, b"x")
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.parametrize("document_id", ["../escape", "a/b", "..", "a b"])
def test_document_ids_must_be_plain(tmp_path, document_id):
    store = FileStore(tmp_path / "files")
    with pytest.raises(InvalidFilenameError):
        store.save(document_id, 1, "x.txt", b"x")
    with pytest.raises(InvalidFilenameError):
        store.remove_document(document_id)


def test_reading_outside_the_root_is_refused(tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    store = FileStore(tmp_path / "files")
    with pytest.raises(InvalidFilenameError):
        store.read("../secret.txt")


def test_remove_version_and_document(tmp_path):
    store = FileStore(tmp_path / "files")
    store.save(DOC, 1, "a.txt", b"1")
    store.save(DOC, 2, "a.txt", b"2")

    store.remove_version(DOC, 1)
    assert not (tmp_path / "files" / DOC / "v1").exists() and (tmp_path / "files" / DOC / "v2").exists()

    store.remove_document(DOC)
    assert not (tmp_path / "files" / DOC).exists()
    store.remove_document(DOC)       # removing something already gone is fine
