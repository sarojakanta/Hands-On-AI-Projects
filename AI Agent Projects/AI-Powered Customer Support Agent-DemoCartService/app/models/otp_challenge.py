"""OTP challenge ORM model.

Persists OTP state so expiry, attempt limits, single-use, and
session/customer binding are enforced entirely in backend code. The LLM never
reads or decides anything about these records.

Note: full OTP verification logic lands in Phase 2. This model is defined now
so the schema is stable and Phase 1 seeding/migrations do not change later.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The browser/session this challenge is bound to. Prevents verification
    # status from leaking across customers/sessions.
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id"), nullable=False, index=True
    )
    # The OTP code is stored for the mock provider only. A real provider would
    # store a hash. Never logged.
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)  # single-use flag

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<OTPChallenge session={self.session_id} customer={self.customer_id}>"
