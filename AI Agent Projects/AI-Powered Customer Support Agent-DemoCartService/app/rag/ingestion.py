"""Document ingestion for the RAG knowledge base (R-1, R-2).

Parses multiple formats and extracts text + metadata:
  * Markdown (.md)  - with optional YAML front-matter,
  * HTML (.html/.htm) - via BeautifulSoup,
  * PDF (.pdf)       - via pypdf,
  * Spreadsheet (.xlsx) - via openpyxl (each row flattened to text).

Every document carries metadata (R-2): policy, version, region, language,
effective_date, customer_safe, owner, source_file. Markdown front-matter
supplies these; other formats fall back to sensible defaults so the pipeline
never produces an un-tagged chunk.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.config import config


_DEFAULT_META = {
    "version": "1.0",
    "region": config.DEFAULT_REGION,
    "language": config.DEFAULT_LANGUAGE,
    "effective_date": "unknown",
    "customer_safe": True,
    "owner": "unknown",
}


def _friendly_name(filename: str) -> str:
    stem = Path(filename).stem
    words = stem.replace("-", " ").replace("_", " ").split()
    special = {"faq": "FAQ"}
    return " ".join(special.get(w.lower(), w.capitalize()) for w in words)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Split YAML front-matter (--- ... ---) from the body. Returns (meta, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")
    return {}, text


# ---- format-specific text extraction ----

def _read_markdown(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    return _parse_front_matter(raw)


def _read_html(path: Path) -> tuple[dict, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    # Preserve headings as section markers so chunking can split on them.
    for tag in soup.find_all(["h1", "h2", "h3"]):
        tag.insert_before("\n## ")
    text = soup.get_text("\n")
    return {}, text


def _read_pdf(path: Path) -> tuple[dict, str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return {}, "\n".join(pages)


def _read_xlsx(path: Path) -> tuple[dict, str]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append(" | ".join(cells))
    return {}, "\n".join(lines)


_READERS = {
    ".md": _read_markdown,
    ".markdown": _read_markdown,
    ".html": _read_html,
    ".htm": _read_html,
    ".pdf": _read_pdf,
    ".xlsx": _read_xlsx,
}


def load_documents(kb_dir: str | None = None) -> list[dict]:
    """Return a list of documents: {filename, text, metadata}.

    Metadata is the merge of defaults + front-matter, with a derived policy name
    and the source filename.
    """
    directory = Path(kb_dir or config.KNOWLEDGE_BASE_DIR)
    docs: list[dict] = []
    if not directory.exists():
        return docs

    for path in sorted(directory.iterdir()):
        reader = _READERS.get(path.suffix.lower())
        if reader is None:
            continue
        front, text = reader(path)
        meta = {**_DEFAULT_META, **(front or {})}
        meta.setdefault("policy", _friendly_name(path.name))
        if not meta.get("policy"):
            meta["policy"] = _friendly_name(path.name)
        meta["source_file"] = path.name
        meta["customer_safe"] = bool(meta.get("customer_safe", True))
        docs.append({"filename": path.name, "text": text, "metadata": meta})
    return docs


def build_chunks(
    kb_dir: str | None = None,
    *,
    customer_safe_only: bool | None = None,
    chunk_tokens: int | None = None,
    overlap_tokens: int | None = None,
):
    """Load and chunk every document in a KB directory (R-1, R-2).

    Args:
        customer_safe_only: when True, only customer-safe docs are chunked;
            when False, only staff docs; when None, all docs.
    """
    from app.rag.chunking import chunk_document

    chunks = []
    for doc in load_documents(kb_dir):
        safe = bool(doc["metadata"].get("customer_safe", True))
        if customer_safe_only is True and not safe:
            continue
        if customer_safe_only is False and safe:
            continue
        chunks.extend(
            chunk_document(
                doc["text"],
                metadata=doc["metadata"],
                chunk_tokens=chunk_tokens,
                overlap_tokens=overlap_tokens,
            )
        )
    return chunks
