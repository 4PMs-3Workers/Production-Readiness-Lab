"""Users share one table and one application role."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import (
    CODE_COLLATION,
    EMAIL_COLLATION,
    TABLE_OPTIONS,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.types import UTCDateTime


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(email)) > 0", name="email_not_empty"),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="name_not_empty"),
        CheckConstraint(
            "CAST(email AS BINARY) = CAST(LOWER(TRIM(email)) AS BINARY)",
            name="email_normalized",
        ),
        CheckConstraint("role IN ('STUDENT', 'STAFF', 'ADMIN')", name="role"),
        CheckConstraint(
            "gender IS NULL OR gender IN ('MALE', 'FEMALE', 'OTHER', 'UNDISCLOSED')", name="gender"
        ),
        CheckConstraint("is_active IN (0, 1)", name="is_active"),
        CheckConstraint(
            "(deleted_at IS NULL AND is_active_before_delete IS NULL) OR "
            "(deleted_at IS NOT NULL AND is_active_before_delete IS NOT NULL AND is_active = 0)",
            name="soft_delete_state",
        ),
        Index("ix_users_deleted_at_role", "deleted_at", "role"),
        TABLE_OPTIONS,
    )

    email: Mapped[str] = mapped_column(String(254, collation=EMAIL_COLLATION), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(10, collation=CODE_COLLATION))
    student_code: Mapped[str | None] = mapped_column(String(50))
    faculty: Mapped[str | None] = mapped_column(String(150))
    major: Mapped[str | None] = mapped_column(String(150))
    gender: Mapped[str | None] = mapped_column(String(20, collation=CODE_COLLATION))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_active_before_delete: Mapped[bool | None] = mapped_column(Boolean)

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        return value.strip().lower()
