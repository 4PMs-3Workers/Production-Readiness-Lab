"""Read the authoritative booking calendar without fallback policy values."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import read_transaction
from app.domain.availability import AvailabilityConfigurationError, AvailabilityPolicy
from app.repositories import availability
from app.schemas.booking_context import BookingContextResponse
from app.services.availability_rules import booking_date_window


async def get_booking_context(
    db: AsyncSession, *, settings: Settings, clock: Callable[[], datetime]
) -> BookingContextResponse:
    """Use one policy read and one clock sample within a bounded read transaction."""
    try:
        async with read_transaction(db, settings):
            setting = await availability.read_settings(db)
            if setting is None:
                raise AvailabilityConfigurationError("Availability settings are missing")
            policy = AvailabilityPolicy(
                timezone=setting.timezone,
                start_step_minutes=setting.start_step_minutes,
                booking_horizon_days=setting.booking_horizon_days,
            )
            business_date, min_date, max_date = booking_date_window(now=clock(), policy=policy)
            return BookingContextResponse(
                timezone=policy.timezone,
                business_date=business_date,
                min_booking_date=min_date,
                max_booking_date=max_date,
            )
    except AvailabilityConfigurationError:
        raise ServiceUnavailable() from None
