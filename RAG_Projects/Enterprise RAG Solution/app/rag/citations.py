"""
Citation extraction and validation.

The model is asked to cite supporting documents as ``[1]``, ``[2]`` ... . This module makes sure
that a citation shown to the user always points at a chunk that was really given to the model:

* markers that refer to a number outside the provided context are removed from the answer,
* the returned ``Citation`` objects are built only from real context items (never from model text).

What this does NOT do: it cannot tell whether a cited chunk truly supports the sentence it is
attached to. That is measured by the evaluation framework (faithfulness / citation correctness).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.types import BuiltContext, Citation

EXCERPT_LENGTH = 300

# 1-2 digit numbers only, so "[2024]" or "[10000]" in normal prose is never mistaken for a citation.
_MARKER = re.compile(r"(?P<lead>[ \t]?)\[(?P<nums>\d{1,2}(?:[ \t]*[,;][ \t]*\d{1,2})*)\]")


@dataclass
class CitationResolution:
    text: str                                              # answer with invalid markers removed
    citations: list[Citation] = field(default_factory=list)
    invalid_numbers: list[int] = field(default_factory=list)


def strip_citation_markers(text: str) -> str:
    """Remove every ``[n]`` marker (used when old answers are shown to the model as history)."""
    return re.sub(r"[ \t]?\[\d{1,2}(?:[ \t]*[,;][ \t]*\d{1,2})*\]", "", text)


def _excerpt(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed if len(collapsed) <= EXCERPT_LENGTH else collapsed[: EXCERPT_LENGTH - 1].rstrip() + "…"


def resolve_citations(answer: str, context: BuiltContext) -> CitationResolution:
    """Validate the markers in ``answer`` against ``context`` and build the citation list."""
    items = {item.number: item for item in context.items}
    cited: list[int] = []
    invalid: list[int] = []

    def replace_marker(match: re.Match[str]) -> str:
        start = match.start()
        # "array[1]" style: a marker glued to a word is not a citation, leave it alone.
        if match.group("lead") == "" and start > 0 and answer[start - 1].isalnum():
            return match.group(0)

        numbers = [int(n) for n in re.findall(r"\d+", match.group("nums"))]
        valid = [n for n in numbers if n in items]
        for n in numbers:
            if n in items:
                if n not in cited:
                    cited.append(n)
            elif n not in invalid:
                invalid.append(n)

        if len(valid) == len(numbers):
            return match.group(0)
        if not valid:
            return ""
        return f"{match.group('lead')}[{', '.join(str(n) for n in valid)}]"

    text = _MARKER.sub(replace_marker, answer)

    citations = [
        Citation(
            number=n,
            document=items[n].chunk.source,
            chunk_id=items[n].chunk.chunk_id,
            page=items[n].chunk.page,
            score=items[n].chunk.score,
            excerpt=_excerpt(items[n].chunk.text),
            document_id=items[n].chunk.document_id,
            version=items[n].chunk.version,
        )
        for n in cited
    ]
    return CitationResolution(text=text, citations=citations, invalid_numbers=invalid)
