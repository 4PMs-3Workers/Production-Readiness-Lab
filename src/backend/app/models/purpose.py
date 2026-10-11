"""Consultation purposes and their current durations."""

from sqlalchemy import Boolean, CheckConstraint, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CODE_COLLATION, TABLE_OPTIONS, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Purpose(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purposes"
    __table_args__ = (
        CheckConstraint("CHAR_LENGTH(TRIM(code)) > 0", name="code_not_empty"),
        CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="name_not_empty"),
        CheckConstraint("duration_minutes > 0", name="duration"),
        CheckConstraint("requires_free_text IN (0, 1)", name="requires_free_text"),
        CheckConstraint("is_active IN (0, 1)", name="is_active"),
        TABLE_OPTIONS,
    )

    code: Mapped[str] = mapped_column(String(50, collation=CODE_COLLATION), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    requires_free_text: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
