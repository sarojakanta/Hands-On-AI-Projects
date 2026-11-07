"""PDF, DOCX and CSV loaders, using files generated in the test (no binary fixtures)."""

import io
import zipfile

import pytest

from app.ingestion.errors import InvalidDocumentError
from app.ingestion.loaders import load_document


def make_pdf(pages: list[str]) -> bytes:
    """Minimal valid PDF with one text line per page (Helvetica)."""
    objects: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_pdf_pages_are_numbered_from_one():
    pages = load_document(make_pdf(["Alpha page text here", "Beta page text here"]), ".pdf")
    assert [(p.page, p.text.strip()) for p in pages] == [(1, "Alpha page text here"), (2, "Beta page text here")]


def test_pdf_without_text_layer_is_rejected_with_a_clear_message():
    with pytest.raises(InvalidDocumentError, match="OCR"):
        load_document(make_pdf([""]), ".pdf")


@pytest.mark.parametrize("data", [b"not a pdf", b"%PDF-1.4 garbage", b""])
def test_pdf_garbage_is_rejected(data):
    with pytest.raises(InvalidDocumentError):
        load_document(data, ".pdf")


def make_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Refund policy", level=1)
    doc.add_paragraph("Customers may return items within 30 days of purchase.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Plan"
    table.rows[0].cells[1].text = "Fee"
    table.rows[1].cells[0].text = "Gold"
    table.rows[1].cells[1].text = "10 dollars"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_headings_paragraphs_and_tables():
    (page,) = load_document(make_docx(), ".docx")
    assert page.page is None
    assert "# Refund policy" in page.text
    assert "within 30 days" in page.text
    assert "| Plan | Fee |" in page.text and "| --- | --- |" in page.text
    assert "| Gold | 10 dollars |" in page.text


def test_docx_that_is_not_a_zip_is_rejected():
    with pytest.raises(InvalidDocumentError):
        load_document(b"plain text", ".docx")


def test_docx_zip_without_word_content_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(InvalidDocumentError):
        load_document(buf.getvalue(), ".docx")


def test_csv_rows_become_header_value_sentences():
    (page,) = load_document(b"name,city\nAda,London\nLin,Taipei\n", ".csv")
    assert page.kind == "records"
    assert page.text.splitlines() == ["name: Ada; city: London.", "name: Lin; city: Taipei."]


def test_csv_semicolon_delimiter_and_bom():
    (page,) = load_document("\ufeffa;b\n1;2\n".encode("utf-8"), ".csv")
    assert page.text == "a: 1; b: 2."


@pytest.mark.parametrize("data", [b"only,header\n", b"", b"a,b\n\n\n", b"\xff\xfe\x00bad"])
def test_csv_without_data_or_binary_is_rejected(data):
    with pytest.raises(InvalidDocumentError):
        load_document(data, ".csv")
