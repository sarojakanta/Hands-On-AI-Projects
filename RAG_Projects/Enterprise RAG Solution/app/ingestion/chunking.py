"""
Text chunking: three strategies behind one entry point, ``chunk_page``.

* ``sentence`` (default, the original behaviour): split at Markdown headings, pack whole sentences.
* ``recursive``: split at headings, then try the coarsest separator that works (blank line, line,
  sentence, word, character) and merge the pieces back up to ``chunk_size`` with real overlap
  between neighbours. Pieces are only cut at a finer level when they do not fit.
* ``structure``: ``recursive`` for prose, but Markdown tables are split by row with the header repeated
  in every chunk, and one-record-per-line data (CSV) is never cut inside a record.

Sections are split at Markdown headings first, so unrelated topics never share a chunk, then each
section is packed sentence by sentence up to ``chunk_size`` characters. (This is the behaviour of
the original engine, moved here so ingestion and the legacy engine share one implementation.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 300
DEFAULT_CHUNK_OVERLAP = 50
MIN_CHUNK_CHARS = 20


STRATEGIES = ("sentence", "recursive", "structure")


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    strategy: str = "sentence"

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown chunking strategy {self.strategy!r}. Use one of: " + ", ".join(STRATEGIES) + ".")
        if self.chunk_size < 1 or not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size.")


def _pack_sentences(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Pack sentences of one section into chunks of at most ~chunk_size characters."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= chunk_size:
            current = (current + " " + sentence).strip() if current else sentence
        else:
            if current:
                chunks.append(current)
                current = ""
            # A single sentence longer than chunk_size is split with overlap.
            if len(sentence) > chunk_size:
                step = max(1, chunk_size - overlap)
                for i in range(0, len(sentence), step):
                    chunks.append(sentence[i : i + chunk_size])
            else:
                current = sentence

    if current:
        chunks.append(current)
    return chunks


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """
    Split text into chunks that never cross a Markdown heading.

    A heading with no body of its own is attached to the section that follows it.
    """
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    sections = re.split(r"\n(?=#{1,6}\s)", text)

    chunks: list[str] = []
    carry = ""   # heading-only sections waiting to be joined with the next section

    for section in sections:
        section = section.strip()
        if not section:
            continue

        has_body = any(not line.startswith("#") and line.strip() for line in section.splitlines())
        if not has_body:
            carry = (carry + "\n" + section).strip()
            continue

        if carry:
            section = carry + "\n" + section
            carry = ""
        chunks.extend(_pack_sentences(section, chunk_size, overlap))

    if carry:
        chunks.append(carry)

    return [c for c in chunks if len(c.strip()) > MIN_CHUNK_CHARS]


# --------------------------------------------------------------------------- recursive strategy

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_HEADING_SPLIT = re.compile(r"\n(?=#{1,6}\s)")


def _split_blank_lines(text: str) -> list[str]:
    return re.split(r"\n\s*\n", text)


def _split_lines(text: str) -> list[str]:
    return text.split("\n")


def _split_sentences(text: str) -> list[str]:
    return _SENTENCE_END.split(text)


def _split_words(text: str) -> list[str]:
    return text.split(" ")


# (splitter, joiner used when pieces are merged back together)
_LEVELS = (
    (_split_blank_lines, "\n\n"),
    (_split_lines, "\n"),
    (_split_sentences, " "),
    (_split_words, " "),
)


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()]


def _merge(pieces: list[str], joiner: str, size: int, overlap: int) -> list[str]:
    """Greedily join pieces up to ``size``; each new chunk starts with the trailing pieces (<= overlap chars)."""
    chunks: list[str] = []
    current: list[str] = []

    def length(items: list[str]) -> int:
        return len(joiner.join(items))

    for piece in pieces:
        if current and length(current + [piece]) > size:
            chunks.append(joiner.join(current))
            while current and (length(current) > overlap or length(current + [piece]) > size):
                current.pop(0)
        current.append(piece)
    if current:
        tail = joiner.join(current)
        # A trailing chunk made only of already-emitted overlap adds nothing.
        if not chunks or not chunks[-1].endswith(tail):
            chunks.append(tail)
    return chunks


def _recursive(text: str, size: int, overlap: int, level: int = 0) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    if level >= len(_LEVELS):
        return _hard_split(text, size, overlap)
    splitter, joiner = _LEVELS[level]
    pieces = [p.strip() for p in splitter(text) if p.strip()]
    if len(pieces) <= 1:
        return _recursive(text, size, overlap, level + 1)
    parts: list[str] = []
    for piece in pieces:
        parts.extend([piece] if len(piece) <= size else _recursive(piece, size, overlap, level + 1))
    return _merge(parts, joiner, size, overlap)


def _sections(text: str) -> list[str]:
    """Split at Markdown headings; a heading with no body is attached to the section after it."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    sections: list[str] = []
    carry = ""
    for section in _HEADING_SPLIT.split(text):
        section = section.strip()
        if not section:
            continue
        has_body = any(not line.startswith("#") and line.strip() for line in section.splitlines())
        if not has_body:
            carry = (carry + "\n" + section).strip()
            continue
        sections.append((carry + "\n" + section).strip() if carry else section)
        carry = ""
    if carry:
        sections.append(carry)
    return sections


def chunk_recursive(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    for section in _sections(text):
        chunks.extend(_recursive(section, chunk_size, overlap))
    return [c for c in chunks if len(c.strip()) > MIN_CHUNK_CHARS]


# --------------------------------------------------------------------------- structure-aware strategy

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
MAX_RECORD_FACTOR = 4   # a record up to this many times chunk_size stays whole; beyond it is split by field


def _split_record(record: str, size: int) -> list[str]:
    """Split an over-long ``Key: value; Key: value.`` record at field boundaries, repeating the first field."""
    fields = [f.strip() for f in record.rstrip(".").split("; ") if f.strip()]
    if len(fields) < 2:
        return _hard_split(record, size, 0)
    lead = fields[0]
    pieces, current = [], [lead]
    for field_text in fields[1:]:
        if len("; ".join(current + [field_text])) > size and len(current) > 1:
            pieces.append("; ".join(current) + ".")
            current = [lead]
        current.append(field_text)
    pieces.append("; ".join(current) + ".")
    return pieces


def chunk_records(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """One record per line. Records are never cut unless a single record is far larger than a chunk."""
    rows: list[str] = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        rows.extend([line] if len(line) <= chunk_size * MAX_RECORD_FACTOR else _split_record(line, chunk_size))
    # A row that does not fit next to others simply becomes its own (larger) chunk.
    return _merge(rows, "\n", chunk_size, overlap)


def _chunk_table(rows: list[str], size: int) -> list[str]:
    """Split a Markdown table by rows; the header (and separator) row is repeated in every chunk."""
    header = rows[:2] if len(rows) > 1 and _TABLE_SEPARATOR.match(rows[1]) else []
    body = rows[len(header):]
    if not body:
        return ["\n".join(rows)]
    chunks, current = [], []
    for row in body:
        if current and len("\n".join(header + current + [row])) > size:
            chunks.append("\n".join(header + current))
            current = []
        current.append(row)
    chunks.append("\n".join(header + current))
    return chunks


def _blocks(section: str) -> list[tuple[str, list[str]]]:
    """Split a section into ("table", rows) and ("text", lines) blocks."""
    blocks: list[tuple[str, list[str]]] = []
    for line in section.splitlines():
        kind = "table" if _TABLE_ROW.match(line) else "text"
        if blocks and blocks[-1][0] == kind:
            blocks[-1][1].append(line)
        else:
            blocks.append((kind, [line]))
    return blocks


def chunk_structured(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    for section in _sections(text):
        blocks = _blocks(section)
        if all(kind == "text" for kind, _ in blocks):
            chunks.extend(_recursive(section, chunk_size, overlap))
            continue
        pending_heading = ""   # keep the section heading attached to what follows
        for kind, lines in blocks:
            block = "\n".join(lines).strip()
            if not block:
                continue
            if kind == "table":
                prefix = (pending_heading + "\n") if pending_heading else ""
                pending_heading = ""
                chunks.extend((prefix + c) if prefix else c for c in _chunk_table([ln.strip() for ln in lines], chunk_size))
            elif all(ln.startswith("#") or not ln.strip() for ln in lines):
                pending_heading = block
            else:
                body = (pending_heading + "\n" + block) if pending_heading else block
                pending_heading = ""
                chunks.extend(_recursive(body, chunk_size, overlap))
    return [c for c in chunks if len(c.strip()) > MIN_CHUNK_CHARS]


# --------------------------------------------------------------------------- entry point


def chunk_page(text: str, config: ChunkingConfig, *, kind: str = "text") -> list[str]:
    """Chunk the text of one page with the configured strategy. ``kind`` comes from the loader."""
    if config.strategy == "structure":
        if kind == "records":
            return [c for c in chunk_records(text, config.chunk_size, config.chunk_overlap) if len(c.strip()) > MIN_CHUNK_CHARS]
        return chunk_structured(text, config.chunk_size, config.chunk_overlap)
    if config.strategy == "recursive":
        return chunk_recursive(text, config.chunk_size, config.chunk_overlap)
    return chunk_text(text, config.chunk_size, config.chunk_overlap)
