"""Customer ORM model."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.db import Base


class Customer(Base):
    __tablename__ = "customers"

    # Business-facing customer reference, e.g. "CUST-1001".
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    # Registered mobile number used by the OTP flow. Never sent to arbitrary
    # numbers; always validated against this value server-side.
    mobile: Mapped[str] = mapped_column(String(20), nullable=False)
    # CRM profile attributes (exposed via get_customer_profile on crm-mcp).
    language: Mapped[str] = mapped_column(String(8), default="en")
    tier: Mapped[str] = mapped_column(String(16), default="STANDARD")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    orders: Mapped[list["Order"]] = relationship(  # noqa: F821
        back_populates="customer", cascade="all, delete-orphan"
    )

    def masked_mobile(self) -> str:
        """Return a masked mobile for safe display (e.g. '+91-XXXXXX1234')."""
        digits = "".join(ch for ch in self.mobile if ch.isdigit())
        if len(digits) <= 4:
            return "XXXX"
        return "XXXXXX" + digits[-4:]

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Customer {self.id} {self.name}>"
