"""Assigned appointment history reads for authenticated staff members."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import read_transaction
from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
)
from app.repositories import appointments
from app.schemas.common import PageResponse
from app.schemas.staff_appointments import (
    StaffAppointmentDetailResponse,
    StaffAppointmentListItem,
    StaffAppointmentListQuery,
)
from app.services.availability_rules import business_day_bounds, validate_policy


def _list_item(row: Any) -> StaffAppointmentListItem:
    return StaffAppointmentListItem.model_validate(
        {name: getattr(row, name) for name in StaffAppointmentListItem.model_fields}
    )


async def list_staff_appointments(
    db: AsyncSession,
    query: StaffAppointmentListQuery,
    *,
    staff_id: UUID,
    settings: Settings,
) -> PageResponse[StaffAppointmentListItem]:
    date_bounds = None
    async with read_transaction(db, settings):
        if query.date is not None:
            stored = await appointments.lock_service_settings(db)
            if stored is None:
                raise ServiceUnavailable()
            try:
                zone = validate_policy(
                    AvailabilityPolicy(
                        timezone=stored.timezone,
                        start_step_minutes=stored.start_step_minutes,
                        booking_horizon_days=stored.booking_horizon_days,
                    )
                )
                bounds = business_day_bounds(selected_date=query.date, zone=zone)
            except BookingDateError as exc:
                raise AppError(422, "VALIDATION_ERROR", "Invalid request", "input") from exc
            except AvailabilityConfigurationError as exc:
                raise ServiceUnavailable() from exc
            date_bounds = (bounds.starts_at, bounds.ends_at)
        rows, total = await appointments.read_staff_appointment_page(
            db, query, staff_id=staff_id, date_bounds=date_bounds
        )
        items = [_list_item(row) for row in rows]
    return PageResponse(items=items, total=total, page=query.page, page_size=query.page_size)


async def get_staff_appointment(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    staff_id: UUID,
    settings: Settings,
) -> StaffAppointmentDetailResponse:
    async with read_transaction(db, settings):
        row = await appointments.find_staff_appointment(
            db, appointment_id=appointment_id, staff_id=staff_id
        )
    if row is None:
        raise AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")
    return StaffAppointmentDetailResponse(
        **{name: getattr(row, name) for name in StaffAppointmentListItem.model_fields},
        description=row.other_reason,
        cancelled_by=row.cancelled_by,
        cancel_reason=row.cancel_reason,
        outcome_by=row.outcome_by,
    )
