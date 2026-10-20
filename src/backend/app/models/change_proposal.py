"""Historical change proposals with at most one pending proposal per appointment."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CODE_COLLATION, TABLE_OPTIONS, Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID, SHA256Hash, UTCDateTime


class ChangeProposal(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "change_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'SUPERSEDED', 'CANCELLED')",
            name="status",
        ),
        CheckConstraint(
            "proposed_ends_at > proposed_starts_at AND expires_at > created_at", name="interval"
        ),
        CheckConstraint(
            "(status = 'PENDING' AND resolved_at IS NULL) OR "
            "(status <> 'PENDING' AND resolved_at IS NOT NULL)",
            name="resolution",
        ),
        UniqueConstraint("appointment_id", "pending_guard", name="uq_change_proposals_pending"),
        UniqueConstraint(
            "appointment_id", "creation_request_key", name="uq_change_proposals_creation_request"
        ),
        UniqueConstraint(
            "appointment_id", "created_version", name="uq_change_proposals_created_version"
        ),
        CheckConstraint(
            "(creation_request_key IS NULL AND creation_request_hash IS NULL "
            "AND created_version IS NULL) OR "
            "(creation_request_key IS NOT NULL AND creation_request_hash IS NOT NULL "
            "AND created_version IS NOT NULL AND CHAR_LENGTH(TRIM(creation_request_key)) > 0 "
            "AND OCTET_LENGTH(creation_request_hash) = 32 "
            "AND created_version BETWEEN 1 AND 9007199254740991)",
            name="creation_request",
        ),
        Index("ix_change_proposals_expiry", "status", "expires_at", "id"),
        Index("ix_change_proposals_created_by", "created_by"),
        Index("ix_change_proposals_resolved_by", "resolved_by"),
        TABLE_OPTIONS,
    )

    appointment_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("appointments.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    created_by: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    creation_request_key: Mapped[str | None] = mapped_column(String(128, collation=CODE_COLLATION))
    creation_request_hash: Mapped[bytes | None] = mapped_column(SHA256Hash())
    created_version: Mapped[int | None] = mapped_column(BigInteger)
    proposed_starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    proposed_ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    proposed_location: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20, collation=CODE_COLLATION), default="PENDING", server_default=text("'PENDING'")
    )
    pending_guard: Mapped[int | None] = mapped_column(
        TINYINT,
        Computed("CASE WHEN status = 'PENDING' THEN 1 ELSE NULL END", persisted=False),
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_by: Mapped[UUID | None] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )


Index(
    "ix_change_proposals_history",
    ChangeProposal.appointment_id,
    ChangeProposal.created_at.desc(),
    ChangeProposal.id.desc(),
)
