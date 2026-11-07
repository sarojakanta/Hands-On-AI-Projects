"""
Build the document context that is placed in the prompt.

Responsibilities
* drop chunks below the similarity threshold,
* drop duplicates (identical text, or text fully contained in an already chosen chunk),
* stay inside a character budget (a cheap proxy for the model's context window),
* number the surviving chunks so the model can cite them as [1], [2], ...,
* neutralise text that could imitate our own ``<document>`` markup.

Retrieved text is untrusted input: it is always wrapped in ``<document>`` elements and the
system prompt tells the model to treat those elements as data, never as instructions.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import replace

from app.rag.types import BuiltContext, ContextItem, RetrievedChunk

_WHITESPACE = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DOCUMENT_TAG = re.compile(r"(?i)<(\s*/?\s*document)")

_ELLIPSIS = "…"
_MIN_USEFUL_CHARS = 50


def escape_document_text(text: str) -> str:
    """Remove control characters and defuse anything that looks like a ``<document>`` tag."""
    return _DOCUMENT_TAG.sub(r"&lt;\1", _CONTROL_CHARS.sub("", text))


def render_document(number: int, chunk: RetrievedChunk) -> str:
    attributes = f'id="{number}" source="{html.escape(chunk.source, quote=True)}"'
    if chunk.chunk_index is not None:
        attributes += f' chunk="{chunk.chunk_index}"'
    if chunk.page is not None:
        attributes += f' page="{chunk.page}"'
    return f"<document {attributes}>\n{escape_document_text(chunk.text)}\n</document>"


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def build_context(
    chunks: Sequence[RetrievedChunk],
    *,
    min_score: float,
    max_chars: int,
    max_items: int,
    lexical_min_coverage: float | None = None,
) -> BuiltContext:
    """Select, deduplicate, budget and number retrieved chunks (best score first)."""
    context = BuiltContext()
    chosen_keys: list[str] = []
    used = 0

    for chunk in sorted(chunks, key=lambda c: c.sort_key, reverse=True):
        if len(context.items) >= max_items:
            break
        keyword_match = (
            lexical_min_coverage is not None
            and chunk.lexical_coverage is not None
            and chunk.lexical_coverage >= lexical_min_coverage
        )
        if chunk.score < min_score and not keyword_match:
            context.dropped_low_score += 1
            continue

        key = _normalise(chunk.text)
        if not key:
            continue
        if any(key == other or key in other or other in key for other in chosen_keys):
            context.dropped_duplicates += 1
            continue

        number = len(context.items) + 1
        rendered_length = len(render_document(number, chunk)) + 2   # + separator

        if used + rendered_length > max_chars:
            if context.items:
                context.dropped_budget += 1
                continue
            # Even the best chunk alone is over budget: keep as much of it as fits.
            overhead = len(render_document(number, replace(chunk, text=""))) + 2
            room = max_chars - overhead - len(_ELLIPSIS)
            if room < _MIN_USEFUL_CHARS:
                context.dropped_budget += 1
                continue
            chunk = replace(chunk, text=chunk.text[:room].rstrip() + _ELLIPSIS)
            context.truncated_first = True
            rendered_length = len(render_document(number, chunk)) + 2

        context.items.append(ContextItem(number=number, chunk=chunk))
        chosen_keys.append(key)
        used += rendered_length

    return context


def format_context(context: BuiltContext) -> str:
    """Render the selected chunks as the ``<document>`` blocks shown to the model."""
    return "\n\n".join(render_document(item.number, item.chunk) for item in context.items)
