"""Audit records; runtime INSERT/SELECT privileges are provisioned separately."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CODE_COLLATION, TABLE_OPTIONS, Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.types import BinaryUUID


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_timeline", "entity_type", "entity_id", "created_at", "id"),
        Index("ix_audit_events_actor_id", "actor_id"),
        TABLE_OPTIONS,
    )

    actor_id: Mapped[UUID | None] = mapped_column(
        BinaryUUID(), ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(60, collation=CODE_COLLATION))
    entity_type: Mapped[str] = mapped_column(String(50, collation=CODE_COLLATION))
    entity_id: Mapped[str] = mapped_column(String(100, collation=CODE_COLLATION))
    request_id: Mapped[str] = mapped_column(String(100, collation=CODE_COLLATION))
    # DeclarativeBase reserves `metadata`; keep the SQL column name unchanged.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON(none_as_null=True), default=dict
    )
