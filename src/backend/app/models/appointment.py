"""Appointment storage; overlap/quota checks require locked service transactions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CODE_COLLATION, TABLE_OPTIONS, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID, SHA256Hash, UTCDateTime


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["staff_id", "purpose_id"],
            ["staff_purposes.staff_id", "staff_purposes.purpose_id"],
            name="fk_appointments_staff_purpose",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('UPCOMING', 'CHANGE_PENDING', 'CANCELLED', 'COMPLETED', 'NO_SHOW')",
            name="status",
        ),
        CheckConstraint(
            "duration_minutes > 0 AND ends_at > starts_at "
            "AND DATE_ADD(starts_at, INTERVAL duration_minutes MINUTE) IS NOT NULL "
            "AND ends_at = DATE_ADD(starts_at, INTERVAL duration_minutes MINUTE)",
            name="interval",
        ),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(creation_request_key)) > 0 "
            "AND OCTET_LENGTH(creation_request_hash) = 32",
            name="creation_request",
        ),
        CheckConstraint(
            "(status = 'CANCELLED' AND cancelled_at IS NOT NULL) OR "
            "(status <> 'CANCELLED' AND cancelled_at IS NULL AND cancel_reason IS NULL)",
            name="cancellation",
        ),
        CheckConstraint(
            "(status IN ('COMPLETED', 'NO_SHOW') AND outcome_at IS NOT NULL) OR "
            "(status NOT IN ('COMPLETED', 'NO_SHOW') AND outcome_at IS NULL)",
            name="outcome",
        ),
        UniqueConstraint(
            "student_id", "creation_request_key", name="uq_appointments_creation_request"
        ),
        Index("ix_appointments_student_interval", "student_id", "status", "starts_at", "ends_at"),
        Index("ix_appointments_staff_interval", "staff_id", "status", "starts_at", "ends_at"),
        # The composite FK needs staff_id, purpose_id as a leftmost prefix.
        Index("ix_appointments_staff_purpose", "staff_id", "purpose_id"),
        Index("ix_appointments_cancelled_by", "cancelled_by"),
        Index("ix_appointments_outcome_by", "outcome_by"),
        TABLE_OPTIONS,
    )

    student_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    staff_id: Mapped[UUID] = mapped_column(BinaryUUID())
    purpose_id: Mapped[UUID] = mapped_column(BinaryUUID())
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    other_reason: Mapped[str | None] = mapped_column(String(2000))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    location: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20, collation=CODE_COLLATION), default="UPCOMING", server_default=text("'UPCOMING'")
    )
    creation_request_key: Mapped[str] = mapped_column(String(128, collation=CODE_COLLATION))
    creation_request_hash: Mapped[bytes] = mapped_column(SHA256Hash())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_by: Mapped[UUID | None] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(2000))
    outcome_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome_by: Mapped[UUID | None] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )


Index(
    "ix_appointments_student_history",
    Appointment.student_id,
    Appointment.starts_at.desc(),
    Appointment.id.desc(),
)
Index("ix_appointments_staff_calendar", Appointment.staff_id, Appointment.starts_at, Appointment.id)
