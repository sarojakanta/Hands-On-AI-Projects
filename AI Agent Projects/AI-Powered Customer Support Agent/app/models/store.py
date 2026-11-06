"""Store ORM model."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


class Store(Base):
    __tablename__ = "stores"

    # Business-facing store reference, e.g. "STORE-01".
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # Human-readable opening hours, e.g. "Mon-Sat 10:00-21:00, Sun 11:00-18:00".
    opening_hours: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Store {self.id} {self.name}>"
