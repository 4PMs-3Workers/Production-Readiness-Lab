"""Users share one table and one application role."""

from sqlalchemy import Boolean, CheckConstraint, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import (
    CODE_COLLATION,
    EMAIL_COLLATION,
    TABLE_OPTIONS,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


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
        CheckConstraint("is_active IN (0, 1)", name="is_active"),
        TABLE_OPTIONS,
    )

    email: Mapped[str] = mapped_column(String(254, collation=EMAIL_COLLATION), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(10, collation=CODE_COLLATION))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        return value.strip().lower()
