"""Declarative registry and consistent MySQL column defaults."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.types import BinaryUUID, UTCDateTime

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}
CODE_COLLATION = "utf8mb4_0900_bin"
EMAIL_COLLATION = "utf8mb4_0900_as_ci"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    # MySQL has no INSERT RETURNING: fetch defaults within flush for AsyncSession.
    __mapper_args__ = {"eager_defaults": True}
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(BinaryUUID(), primary_key=True, default=uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=text("CURRENT_TIMESTAMP(6)")
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
