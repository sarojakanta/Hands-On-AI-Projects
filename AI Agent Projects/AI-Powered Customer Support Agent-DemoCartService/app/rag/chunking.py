"""Token-based, section-aware chunking (R-1) with full metadata (R-2).

Documents are first split into sections on Markdown/HTML-style headings
(lines starting with '#'), then each section is packed into token windows of
CHUNK_TOKENS (300-500) with a small CHUNK_OVERLAP_TOKENS overlap, measured with
tiktoken. Every chunk carries the document's metadata (policy, version, region,
language, effective_date, customer_safe, owner, source_file).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import config


@dataclass
class Chunk:
    id: str
    text: str
    source: str        # human-friendly policy name (for citations)
    source_file: str
    metadata: dict = field(default_factory=dict)


_HEADING_PREFIXES = ("#", "##", "###")


def _get_encoder():
    """Return a tiktoken encoder, or None to fall back to whitespace tokens."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _split_sections(text: str) -> list[str]:
    """Split text into sections on heading lines; keep heading with its body."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        tail = "\n".join(current).strip()
        if tail:
            sections.append(tail)
    return [s for s in sections if s] or ([text.strip()] if text.strip() else [])


def _token_windows(
    text: str, *, size: int, overlap: int, encoder
) -> list[str]:
    """Pack ``text`` into token windows of ``size`` with ``overlap`` overlap."""
    if encoder is not None:
        toks = encoder.encode(text)
        if len(toks) <= size:
            return [text]
        out: list[str] = []
        step = max(size - overlap, 1)
        for start in range(0, len(toks), step):
            window = toks[start : start + size]
            if not window:
                break
            out.append(encoder.decode(window))
            if start + size >= len(toks):
                break
        return out
    # Fallback: approximate tokens by whitespace words.
    words = text.split()
    if len(words) <= size:
        return [text]
    out = []
    step = max(size - overlap, 1)
    for start in range(0, len(words), step):
        window = words[start : start + size]
        if not window:
            break
        out.append(" ".join(window))
        if start + size >= len(words):
            break
    return out


def chunk_document(
    text: str,
    *,
    metadata: dict,
    chunk_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    """Chunk a single document (section-aware, token-based)."""
    size = chunk_tokens or config.CHUNK_TOKENS
    overlap = overlap_tokens if overlap_tokens is not None else config.CHUNK_OVERLAP_TOKENS
    if overlap >= size:
        overlap = max(0, size // 8)

    source = metadata.get("policy", "Policy")
    source_file = metadata.get("source_file", "unknown")
    encoder = _get_encoder()

    chunks: list[Chunk] = []
    idx = 0
    for section in _split_sections(text):
        for window in _token_windows(section, size=size, overlap=overlap, encoder=encoder):
            piece = window.strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    id=f"{source_file}::v{metadata.get('version','?')}::chunk-{idx}",
                    text=piece,
                    source=source,
                    source_file=source_file,
                    metadata=dict(metadata),
                )
            )
            idx += 1
    return chunks


# --- Backward-compatible helper used by older tests ---

def chunk_text(
    text: str,
    *,
    source: str,
    source_file: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    """Legacy char/word chunker kept for existing tests.

    Delegates to the token chunker with a metadata stub. ``chunk_size`` is
    interpreted as an approximate token budget.
    """
    meta = {"policy": source, "source_file": source_file, "version": "1.0"}
    size = max(1, (chunk_size or config.CHUNK_TOKENS) // 4) if chunk_size else config.CHUNK_TOKENS
    return chunk_document(text, metadata=meta, chunk_tokens=size, overlap_tokens=overlap or 0)
