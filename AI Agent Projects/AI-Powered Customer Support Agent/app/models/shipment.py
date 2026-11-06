"""Shipment ORM model."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.db import Base


SHIPMENT_STATUSES = (
    "LABEL_CREATED",
    "IN_TRANSIT",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "DELAYED",
    "RETURNED_TO_ORIGIN",
)


class Shipment(Base):
    __tablename__ = "shipments"

    # Business-facing shipment reference, e.g. "SHP-90001".
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("orders.id"), nullable=False, unique=True, index=True
    )
    courier: Mapped[str] = mapped_column(String(60), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_delivery_date: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    order: Mapped["Order"] = relationship(back_populates="shipment")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Shipment {self.id} {self.status}>"
