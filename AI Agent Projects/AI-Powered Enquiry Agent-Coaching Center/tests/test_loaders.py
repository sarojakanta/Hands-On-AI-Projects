"""Tests for document loaders (TXT / MD / DOCX / unsupported)."""
from __future__ import annotations

import pytest

from app.rag.loaders import UnsupportedDocumentError, load_document


def test_load_txt(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("Admission policy and fees.", encoding="utf-8")
    assert "Admission policy" in load_document(str(p))


def test_load_markdown(tmp_path):
    p = tmp_path / "faq.md"
    p.write_text("# FAQ\n\nQ: Fees?\nA: 15000.", encoding="utf-8")
    text = load_document(str(p))
    assert "Fees?" in text


def test_load_docx_including_tables(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "doc.docx"
    document = docx.Document()
    document.add_paragraph("SGEN Computer Academy prospectus.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Course"
    table.rows[0].cells[1].text = "Python Programming"
    document.save(str(p))

    text = load_document(str(p))
    assert "prospectus" in text
    # Table cell content is included (joined with | ).
    assert "Python Programming" in text


def test_unsupported_extension_raises(tmp_path):
    p = tmp_path / "evil.exe"
    p.write_bytes(b"MZ...")
    with pytest.raises(UnsupportedDocumentError):
        load_document(str(p))


def test_unknown_extension_raises(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b,c", encoding="utf-8")
    with pytest.raises(UnsupportedDocumentError):
        load_document(str(p))
