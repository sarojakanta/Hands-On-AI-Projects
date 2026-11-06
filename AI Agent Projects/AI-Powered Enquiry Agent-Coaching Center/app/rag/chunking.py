"""Text chunking for RAG.

Uses LangChain's RecursiveCharacterTextSplitter, which splits on natural
boundaries (paragraphs, lines, sentences) before falling back to characters,
producing coherent, overlapping chunks.
"""
from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """Split text into overlapping chunks."""
    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size or settings.rag_chunk_size,
        chunk_overlap=overlap if overlap is not None else settings.rag_chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )
    chunks = splitter.split_text(text or "")
    return [c.strip() for c in chunks if c.strip()]
