"""Per-recipient notification state; content is resolved from authorized data."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CODE_COLLATION, TABLE_OPTIONS, Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID, UTCDateTime


class Notification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_appointment_id", "appointment_id"), TABLE_OPTIONS)

    recipient_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    appointment_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("appointments.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(50, collation=CODE_COLLATION))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


Index(
    "ix_notifications_recipient_history",
    Notification.recipient_id,
    Notification.created_at.desc(),
    Notification.id.desc(),
)
