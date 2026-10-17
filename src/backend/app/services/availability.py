"""Availability service using one bounded, read-only transaction."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_error
from app.api.errors import ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import read_transaction
from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
    TimeInterval,
)
from app.repositories import availability, catalog
from app.schemas.availability import (
    AvailabilityQuery,
    AvailabilityResponse,
    AvailableSlot,
)
from app.schemas.common import UtcTimestamp
from app.services.availability_rules import (
    business_day_bounds,
    generate_slots,
    validate_booking_date,
    validate_policy,
)


async def get_availability(
    db: AsyncSession,
    query: AvailabilityQuery,
    *,
    student_id: UUID,
    settings: Settings,
    clock: Callable[[], datetime],
) -> AvailabilityResponse:
    """Calculate a student's current slots from bounded database reads."""
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
            zone = validate_policy(policy)
            now = clock()
            validate_booking_date(selected_date=query.date, now=now, policy=policy)

            purpose = await catalog.find_active_purpose(db, query.purpose_id)
            if purpose is None:
                raise catalog_error("NOT_FOUND")
            eligible_staff = await catalog.find_accepting_staff(
                db, query.purpose_id, query.staff_id
            )
            if eligible_staff is None:
                raise catalog_error("NOT_FOUND")

            bounds = business_day_bounds(selected_date=query.date, zone=zone)
            windows = await availability.read_windows(db, query.date.isoweekday())
            unavailability = await availability.read_unavailability(
                db, staff_id=query.staff_id, bounds=bounds
            )
            staff_appointments = await availability.read_staff_appointments(
                db, staff_id=query.staff_id, bounds=bounds
            )
            student_appointments = await availability.read_student_appointments(
                db, student_id=student_id, bounds=bounds
            )
            busy: tuple[TimeInterval, ...] = (
                *unavailability,
                *staff_appointments,
                *student_appointments,
            )
            slots = generate_slots(
                selected_date=query.date,
                now=now,
                policy=policy,
                duration_minutes=purpose.duration_minutes,
                windows=windows,
                busy=busy,
            )
            response = AvailabilityResponse(
                date=query.date,
                timezone=policy.timezone,
                slots=[
                    AvailableSlot(
                        starts_at=UtcTimestamp(slot.starts_at),
                        ends_at=UtcTimestamp(slot.ends_at),
                    )
                    for slot in slots
                ],
            )
    except BookingDateError:
        raise catalog_error("VALIDATION_ERROR") from None
    except AvailabilityConfigurationError:
        raise ServiceUnavailable() from None
    return response
