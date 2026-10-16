"""Owned student history reads and explicit public detail mapping."""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.repositories import appointments
from app.schemas.appointments import (
    AppointmentDetailResponse,
    AppointmentListItem,
    AppointmentListQuery,
)
from app.schemas.common import PageResponse


def _list_item(row: Any) -> AppointmentListItem:
    return AppointmentListItem.model_validate(
        {name: getattr(row, name) for name in AppointmentListItem.model_fields}
    )


async def list_student_appointments(
    db: AsyncSession, query: AppointmentListQuery, *, student_id: UUID
) -> PageResponse[AppointmentListItem]:
    try:
        async with db.begin():
            rows, total = await appointments.read_student_appointment_page(
                db, query, student_id=student_id
            )
            items = [_list_item(row) for row in rows]
    except SQLAlchemyError:
        raise ServiceUnavailable() from None
    return PageResponse(items=items, total=total, page=query.page, page_size=query.page_size)


async def get_student_appointment(
    db: AsyncSession, *, appointment_id: UUID, student_id: UUID
) -> AppointmentDetailResponse:
    try:
        row = await appointments.find_student_appointment(
            db, appointment_id=appointment_id, student_id=student_id
        )
    except SQLAlchemyError:
        raise ServiceUnavailable() from None
    if row is None:
        raise AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")
    return AppointmentDetailResponse(
        **{name: getattr(row, name) for name in AppointmentListItem.model_fields},
        description=row.other_reason,
        cancelled_at=row.cancelled_at,
        cancel_reason=row.cancel_reason,
    )
