"""Periods during which a staff member does not accept appointments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TABLE_OPTIONS, Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID, UTCDateTime


class StaffUnavailability(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "staff_unavailability"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="interval"),
        Index("ix_staff_unavailability_interval", "staff_id", "starts_at", "ends_at"),
        TABLE_OPTIONS,
    )

    staff_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
