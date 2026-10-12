"""Singleton school-wide booking settings."""

from datetime import datetime

from sqlalchemy import CheckConstraint, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TABLE_OPTIONS, Base, utc_now
from app.models.types import UTCDateTime


class ServiceSetting(Base):
    __tablename__ = "service_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("start_step_minutes > 0", name="start_step"),
        CheckConstraint("booking_horizon_days > 0", name="booking_horizon"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=1, server_default=text("1")
    )
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Ho_Chi_Minh", server_default=text("'Asia/Ho_Chi_Minh'")
    )
    start_step_minutes: Mapped[int] = mapped_column(
        SmallInteger, default=15, server_default=text("15")
    )
    booking_horizon_days: Mapped[int] = mapped_column(
        SmallInteger, default=7, server_default=text("7")
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
