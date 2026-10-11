"""Retained staff-purpose associations, including inactive acceptance."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TABLE_OPTIONS, Base, TimestampMixin
from app.models.types import BinaryUUID


class StaffPurpose(TimestampMixin, Base):
    __tablename__ = "staff_purposes"
    __table_args__ = (
        CheckConstraint("is_accepting IN (0, 1)", name="is_accepting"),
        Index("ix_staff_purposes_accepting", "purpose_id", "is_accepting", "staff_id"),
        TABLE_OPTIONS,
    )

    staff_id: Mapped[UUID] = mapped_column(
        BinaryUUID(),
        ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        primary_key=True,
    )
    purpose_id: Mapped[UUID] = mapped_column(
        BinaryUUID(),
        ForeignKey("purposes.id", ondelete="RESTRICT", onupdate="RESTRICT"),
        primary_key=True,
    )
    is_accepting: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
