"""
Turn uploaded bytes into text pages.

Supported: .txt, .md, .pdf (per-page text, no OCR), .docx and .csv. The extension -> loader table is the
extension point for further formats: add a function and register it; nothing else changes.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from app.ingestion.errors import InvalidDocumentError


@dataclass(frozen=True)
class PageText:
    page: int | None   # None for formats without pages
    text: str
    kind: str = "text"   # "records": one self-contained record per line (CSV); the chunker never cuts a record


def _load_plain_text(data: bytes) -> list[PageText]:
    try:
        text = data.decode("utf-8-sig")   # also strips a UTF-8 byte-order mark
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("File must be UTF-8 encoded text.") from exc
    if "\x00" in text:
        raise InvalidDocumentError("File does not look like a text document.")
    if not text.strip():
        raise InvalidDocumentError("File contains no text.")
    return [PageText(page=None, text=text)]


MAX_PDF_PAGES = 500
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024   # guards against zip bombs; uploads are capped far below this
MAX_CSV_ROWS = 50_000


def _require_text(pages: list[PageText], hint: str) -> list[PageText]:
    pages = [p for p in pages if p.text.strip()]
    if not pages:
        raise InvalidDocumentError(hint)
    return pages


def _load_pdf(data: bytes) -> list[PageText]:
    """Extract text page by page (page numbers start at 1). Scanned PDFs have no text layer: no OCR."""
    if not data.startswith(b"%PDF"):
        raise InvalidDocumentError("File does not look like a PDF document.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:   # pragma: no cover - pinned in requirements.txt
        raise InvalidDocumentError("PDF support requires the 'pypdf' package.") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise InvalidDocumentError("Password-protected PDF files are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InvalidDocumentError(f"PDF has more than {MAX_PDF_PAGES} pages.")
        pages = [PageText(page=i, text=(page.extract_text() or "")) for i, page in enumerate(reader.pages, start=1)]
    except InvalidDocumentError:
        raise
    except Exception as exc:   # noqa: BLE001 - pypdf raises many types for malformed files
        raise InvalidDocumentError("The PDF file could not be read.") from exc
    return _require_text(pages, "No text found in this PDF (scanned documents need OCR, which is not supported).")


def _load_docx(data: bytes) -> list[PageText]:
    """Paragraphs (headings become Markdown headings so chunking respects them) and table rows."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
                raise InvalidDocumentError("The DOCX file expands to an unreasonable size.")
    except zipfile.BadZipFile as exc:
        raise InvalidDocumentError("File does not look like a DOCX document.") from exc
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:   # pragma: no cover
        raise InvalidDocumentError("DOCX support requires the 'python-docx' package.") from exc
    try:
        document = Document(io.BytesIO(data))
        lines: list[str] = []
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                style = (paragraph.style.name or "") if paragraph.style is not None else ""
                match = re.match(r"Heading (\d)$", style)
                lines.append(f"{'#' * int(match.group(1))} {text}" if match else text)
            elif tag == "tbl":
                # Markdown pipe table: readable as text and recognised by the structure-aware chunker.
                header_done = False
                for row in Table(child, document).rows:
                    cells = [c.text.strip().replace("|", "/").replace("\n", " ") for c in row.cells]
                    if not any(cells):
                        continue
                    lines.append("| " + " | ".join(cells) + " |")
                    if not header_done:
                        lines.append("| " + " | ".join("---" for _ in cells) + " |")
                        header_done = True
    except InvalidDocumentError:
        raise
    except Exception as exc:   # noqa: BLE001
        raise InvalidDocumentError("The DOCX file could not be read.") from exc
    return _require_text([PageText(page=None, text="\n".join(lines))], "File contains no text.")


def _load_csv(data: bytes) -> list[PageText]:
    """Each row becomes one self-describing sentence: ``Header: value; Header: value.``"""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("File must be UTF-8 encoded text.") from exc
    if "\x00" in text:
        raise InvalidDocumentError("File does not look like a text document.")
    try:
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(itertools.islice(csv.reader(io.StringIO(text), dialect), MAX_CSV_ROWS + 2))
    except csv.Error as exc:
        raise InvalidDocumentError("The CSV file could not be parsed.") from exc
    if len(rows) > MAX_CSV_ROWS + 1:
        raise InvalidDocumentError(f"CSV has more than {MAX_CSV_ROWS} rows.")
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise InvalidDocumentError("CSV needs a header row and at least one data row.")
    header = [h.strip() or f"column{i + 1}" for i, h in enumerate(rows[0])]
    lines = []
    for row in rows[1:]:
        pairs = [f"{header[i] if i < len(header) else f'column{i + 1}'}: {c.strip()}" for i, c in enumerate(row) if c.strip()]
        if pairs:
            lines.append("; ".join(pairs) + ".")
    return _require_text([PageText(page=None, text="\n".join(lines), kind="records")], "File contains no data rows.")


LOADERS: dict[str, Callable[[bytes], list[PageText]]] = {
    ".txt": _load_plain_text,
    ".md": _load_plain_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".csv": _load_csv,
}
SUPPORTED_EXTENSIONS = frozenset(LOADERS)


def load_document(data: bytes, extension: str) -> list[PageText]:
    loader = LOADERS.get(extension.lower())
    if loader is None:
        raise InvalidDocumentError(f"Unsupported file type '{extension}'.")
    return loader(data)
