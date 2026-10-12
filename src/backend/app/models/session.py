"""Persisted authentication sessions; raw tokens are never stored."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TABLE_OPTIONS, Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID, SHA256Hash, UTCDateTime


class Session(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("OCTET_LENGTH(token_hash) = 32", name="token_hash"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
        TABLE_OPTIONS,
    )

    user_id: Mapped[UUID] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    token_hash: Mapped[bytes] = mapped_column(SHA256Hash(), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
