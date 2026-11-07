"""Recursive and structure-aware chunking, strategy configuration, and the upgrade of old databases."""

import sqlite3

import pytest

from app.core.config import Settings
from app.db.session import Database
from app.ingestion.chunking import (
    ChunkingConfig,
    chunk_page,
    chunk_records,
    chunk_recursive,
    chunk_structured,
)
from tests.unit.test_ingestion_service import build_kb, utf8

PROSE = (
    "Alpha is the first paragraph. It has two sentences that explain alpha in some detail.\n\n"
    "Beta is the second paragraph. It talks about beta and what beta does every single day.\n\n"
    "Gamma is the third paragraph. It describes gamma, which is unrelated to alpha or beta."
)


# ---------------------------------------------------------------- configuration


def test_config_validates_strategy_and_overlap():
    assert ChunkingConfig().strategy == "sentence"
    with pytest.raises(ValueError):
        ChunkingConfig(strategy="semantic")
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=100)
    assert Settings(_env_file=None, chunk_strategy="recursive").chunk_strategy == "recursive"
    with pytest.raises(Exception):
        Settings(_env_file=None, chunk_strategy="semantic")


def test_default_strategy_is_the_original_behaviour():
    from app.ingestion.chunking import chunk_text

    assert chunk_page(PROSE, ChunkingConfig(150, 20)) == chunk_text(PROSE, 150, 20)


# ---------------------------------------------------------------- recursive


def test_recursive_prefers_paragraph_boundaries():
    chunks = chunk_recursive(PROSE, 120, 0)
    assert all(len(c) <= 120 for c in chunks)
    assert [c.split(" is ")[0] for c in chunks] == ["Alpha", "Beta", "Gamma"]


def test_recursive_adds_real_overlap_between_neighbours():
    text = " ".join(f"Sentence number {i} is about topic {i}." for i in range(40))
    chunks = chunk_recursive(text, 150, 50)
    assert len(chunks) > 3 and all(len(c) <= 150 for c in chunks)
    for left, right in zip(chunks, chunks[1:]):
        shared = left.split(". ")[-1]
        assert shared in right, "neighbouring chunks should share trailing text"
    no_overlap = chunk_recursive(text, 150, 0)
    for left, right in zip(no_overlap, no_overlap[1:]):
        assert left.split(". ")[-1] not in right


def test_recursive_loses_no_content_and_cuts_only_when_needed():
    text = " ".join(f"Sentence number {i} is about topic {i}." for i in range(40))
    chunks = chunk_recursive(text, 150, 50)
    joined = " ".join(chunks)
    assert all(f"Sentence number {i} " in joined for i in range(40))
    assert all(c.endswith(".") for c in chunks)            # never cut inside a sentence here


def test_recursive_falls_back_to_words_then_characters():
    long_sentence = "word " * 100 + "end."
    words = chunk_recursive(long_sentence, 120, 20)
    assert len(words) > 3 and all(len(c) <= 120 for c in words)
    assert all(not c.startswith("ord") for c in words)      # split on spaces, not mid-word
    blob = "x" * 500
    assert all(len(c) <= 100 for c in chunk_recursive(blob, 100, 10))


def test_recursive_never_crosses_headings():
    text = "# One\n\nThe first section has its own text and enough of it.\n\n# Two\n\nThe second section is separate text as well."
    chunks = chunk_recursive(text, 500, 50)
    assert len(chunks) == 2 and chunks[0].startswith("# One") and chunks[1].startswith("# Two")


def test_recursive_edge_cases():
    assert chunk_recursive("", 100, 10) == []
    assert chunk_recursive("tiny", 100, 10) == []
    assert chunk_recursive(PROSE, 120, 30) == chunk_recursive(PROSE, 120, 30)


# ---------------------------------------------------------------- structure: tables


TABLE = "\n".join(
    ["# Prices", "", "| Plan | Fee | Users |", "| --- | --- | --- |"]
    + [f"| Plan{i} | {i * 10} dollars | {i * 5} |" for i in range(1, 13)]
)


def test_tables_split_by_row_with_the_header_repeated():
    chunks = chunk_structured(TABLE, 150, 0)
    assert len(chunks) > 1
    for chunk in chunks:
        assert "| Plan | Fee | Users |" in chunk and "| --- | --- | --- |" in chunk
        assert len(chunk) <= 150 + len("# Prices\n")
        for line in chunk.splitlines():
            assert line.startswith("#") or line.startswith("|") and line.endswith("|")   # no row is ever cut
    rows = [ln for c in chunks for ln in c.splitlines() if ln.startswith("| Plan") and "Fee" not in ln]
    assert len(rows) == 12 and len(set(rows)) == 12


def test_section_heading_stays_with_its_table():
    assert chunk_structured(TABLE, 150, 0)[0].startswith("# Prices\n| Plan |")


def test_prose_around_a_table_is_chunked_as_prose():
    text = "Intro paragraph that explains the prices below in a few words.\n\n" + "\n".join(
        ["| A | B |", "| --- | --- |", "| 1 | 2 |"]
    ) + "\n\nClosing paragraph that thanks the reader for reading the table."
    chunks = chunk_structured(text, 500, 0)
    assert any(c.startswith("Intro") for c in chunks) and any("| 1 | 2 |" in c for c in chunks)
    assert any("Closing" in c for c in chunks)


# ---------------------------------------------------------------- structure: records


def _records(n):
    return "\n".join(f"id: {i}; name: Customer {i}; city: Town {i}; plan: Gold." for i in range(n))


def test_records_are_never_cut_and_all_survive():
    text = _records(30)
    chunks = chunk_records(text, 200, 0)
    lines = [ln for c in chunks for ln in c.splitlines()]
    assert lines == text.splitlines()
    assert all(len(c) <= 200 for c in chunks)


def test_records_overlap_repeats_trailing_rows():
    chunks = chunk_records(_records(30), 200, 90)
    for left, right in zip(chunks, chunks[1:]):
        assert left.splitlines()[-1] == right.splitlines()[0]


def test_an_oversized_record_is_split_at_field_boundaries_and_keeps_its_key():
    record = "id: 7; " + "; ".join(f"field{i}: {'v' * 40}" for i in range(60)) + "."
    chunks = chunk_records(record, 100, 0)
    assert len(chunks) > 3
    assert all(c.startswith("id: 7; ") and c.endswith(".") for c in chunks)
    assert all(len(c) <= 100 + 60 for c in chunks)


def test_oversized_but_reasonable_record_stays_whole():
    record = "id: 1; note: " + "word " * 60 + "."          # 300+ chars but under 4x chunk size
    (chunk,) = chunk_records(record, 200, 0)
    assert chunk == record.strip()


def test_page_kind_selects_the_records_chunker():
    cfg = ChunkingConfig(200, 0, "structure")
    assert chunk_page(_records(10), cfg, kind="records") == chunk_records(_records(10), 200, 0)
    assert chunk_page(_records(10), cfg) == chunk_structured(_records(10), 200, 0)   # as plain text
    sentence = ChunkingConfig(200, 0, "sentence")
    assert chunk_page(_records(10), sentence, kind="records") != chunk_records(_records(10), 200, 0)


# ---------------------------------------------------------------- end to end through ingestion


def csv_bytes(n=25):
    rows = ["id,name,city,plan"] + [f"{i},Customer {i},Town {i},Gold" for i in range(n)]
    return ("\n".join(rows) + "\n").encode()


def test_structure_strategy_is_recorded_per_version_and_flags_outdated_documents(tmp_path):
    kb = build_kb(tmp_path)
    created = kb.service.ingest("people.csv", csv_bytes())
    assert created.version.chunk_strategy == "sentence"
    assert created.document.chunking_outdated is False

    kb.service._chunking = ChunkingConfig(300, 50, "structure")
    assert kb.service.get_document(created.document.id).chunking_outdated is True

    result = kb.service.reindex_document(created.document.id)
    assert result.version.chunk_strategy == "structure" and result.document.chunking_outdated is False
    texts = [h.text for h in kb.store.query(kb.service.collection, kb.embedder.embed_query("Customer 3"), 50)]
    lines = [ln for t in texts for ln in t.splitlines()]
    assert all(ln.startswith("id: ") and ln.endswith(".") for ln in lines)   # no record was cut
    assert kb.service.index_report().consistent


def test_markdown_table_upload_keeps_headers_with_every_chunk(tmp_path):
    kb = build_kb(tmp_path)
    kb.service._chunking = ChunkingConfig(150, 0, "structure")
    kb.service.ingest("prices.md", utf8(TABLE))
    hits = kb.store.query(kb.service.collection, kb.embedder.embed_query("Plan fee"), 50)
    assert len(hits) > 1 and all("| Plan | Fee | Users |" in h.text for h in hits)


# ---------------------------------------------------------------- existing databases are upgraded


def test_database_created_before_the_strategy_column_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    first = Database(f"sqlite:///{path.as_posix()}")
    first.create_all()
    first.dispose()
    con = sqlite3.connect(path)
    con.execute("INSERT INTO documents (id, name, name_key, source_type, created_at, updated_at) "
                "VALUES ('d1','a.txt','a.txt','upload','2026-01-01','2026-01-01')")
    con.execute("ALTER TABLE document_versions DROP COLUMN chunk_strategy")
    con.commit()
    assert "chunk_strategy" not in [r[1] for r in con.execute("PRAGMA table_info(document_versions)")]
    con.close()

    upgraded = Database(f"sqlite:///{path.as_posix()}")
    upgraded.create_all()
    upgraded.create_all()   # idempotent
    upgraded.dispose()
    con = sqlite3.connect(path)
    info = {r[1]: r for r in con.execute("PRAGMA table_info(document_versions)")}
    assert "chunk_strategy" in info and info["chunk_strategy"][3] == 1 and "sentence" in str(info["chunk_strategy"][4])
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 1   # data untouched
    con.close()
