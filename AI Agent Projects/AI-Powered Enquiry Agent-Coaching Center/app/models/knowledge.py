"""KnowledgeDocument model (RAG source documents)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.institute import Institute


class KnowledgeDocument(Base, TimestampMixin):
    """An institute document ingested into the RAG knowledge base."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # pending / indexed / failed
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    institute: Mapped["Institute"] = relationship(back_populates="documents")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<KnowledgeDocument id={self.id} filename={self.filename!r} status={self.status!r}>"
