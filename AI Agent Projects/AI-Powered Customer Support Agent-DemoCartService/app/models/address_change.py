"""Address-change request model.

A guarded write in the OMS domain (oms-mcp.request_address_change). We never
mutate the order directly from the agent; instead we record a REQUEST that is
"checked by rules" and handled downstream. This keeps the sensitive operation
auditable and reversible.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


ADDRESS_CHANGE_STATUSES = ("REQUESTED", "APPROVED", "REJECTED", "APPLIED")


class AddressChangeRequest(Base):
    __tablename__ = "address_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("orders.id"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=False, index=True
    )
    new_address: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AddressChangeRequest order={self.order_id} status={self.status}>"
