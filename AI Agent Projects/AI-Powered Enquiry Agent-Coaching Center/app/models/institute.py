"""Institute (tenant root) and AdminUser models."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.conversation import Conversation
    from app.models.knowledge import KnowledgeDocument
    from app.models.lead import StudentLead


class Institute(Base, TimestampMixin):
    """A coaching centre / training institute. The tenant root.

    Every tenant-scoped table references ``institute_id``. Even though the
    MVP runs a single institute, keeping this relationship everywhere makes
    evolving to full multi-tenant a configuration change, not a migration.
    """

    __tablename__ = "institutes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    # Public API key used by the embeddable widget to resolve the tenant.
    api_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Relationships
    admins: Mapped[list["AdminUser"]] = relationship(
        back_populates="institute", cascade="all, delete-orphan"
    )
    courses: Mapped[list["Course"]] = relationship(
        back_populates="institute", cascade="all, delete-orphan"
    )
    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        back_populates="institute", cascade="all, delete-orphan"
    )
    leads: Mapped[list["StudentLead"]] = relationship(
        back_populates="institute", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="institute", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Institute id={self.id} slug={self.slug!r}>"


class AdminUser(Base, TimestampMixin):
    """A staff member who can log into the admin dashboard."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(
        ForeignKey("institutes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    institute: Mapped["Institute"] = relationship(back_populates="admins")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AdminUser id={self.id} email={self.email!r}>"
