"""Document loading (PDF, DOCX, TXT, Markdown) and chunking."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

SUPPORTED_TYPES = {".md": "md", ".markdown": "md", ".txt": "txt", ".pdf": "pdf", ".docx": "docx"}
MAX_BYTES = 20 * 1024 * 1024
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class Segment:
    text: str
    page: int | None = None


@dataclass
class Chunk:
    text: str
    index: int
    section: str = ""
    page: int | None = None
    extra: dict = field(default_factory=dict)


def file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_TYPES:
        raise UnsupportedDocumentError(
            f"Unsupported file type '{ext or filename}'. Supported: {', '.join(sorted(set(SUPPORTED_TYPES)))}")
    return SUPPORTED_TYPES[ext]


def clean(text: str) -> str:
    return _CTRL.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def load_segments(filename: str, data: bytes) -> list[Segment]:
    """Extract text segments from a document. Content is treated as untrusted data."""
    kind = file_type(filename)
    if len(data) > MAX_BYTES:
        raise UnsupportedDocumentError(f"Document exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    if kind in ("md", "txt"):
        return [Segment(clean(data.decode("utf-8", errors="replace")))]
    if kind == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return [Segment(clean(page.extract_text() or ""), page=i + 1) for i, page in enumerate(reader.pages)]
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedDocumentError(f"Could not read PDF: {type(exc).__name__}") from exc
    try:
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                style = (para.style.name or "") if para.style is not None else ""
                prefix = "# " if style.lower().startswith("heading") else ""
                parts.append(prefix + para.text)
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        return [Segment(clean("\n".join(parts)))]
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedDocumentError(f"Could not read DOCX: {type(exc).__name__}") from exc


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def chunk_segments(segments: list[Segment], size: int | None = None, overlap: int | None = None) -> list[Chunk]:
    """Split text on paragraph/line boundaries into ~``size`` char chunks with overlap.

    Markdown headings are tracked so each chunk knows its section (used in citations).
    """
    size = size or settings.chunk_size
    overlap = min(overlap if overlap is not None else settings.chunk_overlap, size // 2)
    chunks: list[Chunk] = []
    for seg in segments:
        section = ""
        buf: list[str] = []
        buf_len = 0
        buf_section = ""
        fresh = False  # buffer holds new text, not just carried-over overlap

        def flush():
            nonlocal buf, buf_len, buf_section, fresh
            text = "\n".join(buf).strip()
            if text and fresh:
                chunks.append(Chunk(text=text, index=len(chunks), section=buf_section, page=seg.page))  # noqa: B023 - flush() runs inside the loop iteration
            tail = text[-overlap:] if overlap and text and fresh else ""
            buf, buf_len, buf_section, fresh = ([tail] if tail else []), len(tail), section, False  # noqa: B023

        for line in seg.text.split("\n"):
            m = _HEADING.match(line)
            if m:
                section = m.group(1)
            pieces = [line[i:i + size] for i in range(0, len(line), size)] or [""]
            for piece in pieces:
                if buf_len + len(piece) + 1 > size and buf:
                    flush()
                if not buf:
                    buf_section = section
                buf.append(piece)
                buf_len += len(piece) + 1
                fresh = fresh or bool(piece.strip())
                if m:  # a heading inside the chunk makes it the most specific section label
                    buf_section = section
        flush()
    # drop overlap-only fragments and re-index
    result = [c for c in chunks if len(c.text.strip()) > 1]
    for i, c in enumerate(result):
        c.index = i
    return result
