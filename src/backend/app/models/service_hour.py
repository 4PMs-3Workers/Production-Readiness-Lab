"""Weekly service windows in the configured business timezone."""

from datetime import time

from sqlalchemy import CheckConstraint, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TABLE_OPTIONS, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import TimeOfDay


class ServiceHour(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "service_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday"),
        CheckConstraint(
            "opens_at >= '00:00:00' AND opens_at < closes_at AND closes_at < '24:00:00'",
            name="interval",
        ),
        UniqueConstraint("weekday", "opens_at", "closes_at", name="uq_service_hours_window"),
        TABLE_OPTIONS,
    )

    weekday: Mapped[int] = mapped_column(SmallInteger)
    opens_at: Mapped[time] = mapped_column(TimeOfDay())
    closes_at: Mapped[time] = mapped_column(TimeOfDay())
