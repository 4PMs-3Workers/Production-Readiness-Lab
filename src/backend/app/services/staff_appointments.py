"""Assigned appointment reads and outcome recording for authenticated staff members."""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    mysql_error_code,
    read_transaction,
    rollback,
)
from app.domain.auth import AuthContext
from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
)
from app.models import AuditEvent, Notification
from app.repositories import appointments, users
from app.schemas.common import PageResponse, UtcTimestamp
from app.schemas.staff_appointments import (
    StaffAppointmentDetailResponse,
    StaffAppointmentListItem,
    StaffAppointmentListQuery,
    StaffAppointmentOutcomeRequest,
    StaffAppointmentOutcomeResponse,
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


async def _record_outcome_attempt(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    outcome: StaffAppointmentOutcomeRequest,
    context: AuthContext,
    request_id: str,
    settings: Settings,
) -> StaffAppointmentOutcomeResponse:
    transaction = await db.begin()
    try:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            service_setting = await appointments.lock_service_settings(db)
        if service_setting is None:
            raise ServiceUnavailable()
        identities = await appointments.find_staff_appointment_accounts(
            db, appointment_id=appointment_id, staff_id=context.user_id
        )
        if identities is None:
            raise AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")
        locked_users = {}
        for account_id in sorted(
            {identities.student_id, identities.staff_id}, key=lambda item: item.bytes
        ):
            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                locked_users[account_id] = await users.lock_by_id(db, account_id)
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            row = await appointments.lock_appointment(db, appointment_id)
        actor = locked_users.get(context.user_id)
        if (
            row is None
            or row.staff_id != context.user_id
            or row.student_id != identities.student_id
            or actor is None
            or actor.role != "STAFF"
            or not actor.is_active
        ):
            raise AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")
        if row.status in {"COMPLETED", "NO_SHOW"}:
            if row.status == outcome.status:
                if row.outcome_at is None or row.outcome_by != context.user_id:
                    raise ServiceUnavailable()
                result = StaffAppointmentOutcomeResponse(
                    id=row.id, status=outcome.status, outcome_at=UtcTimestamp(row.outcome_at)
                )
                await rollback(db)
                return result
            raise AppError(
                409,
                "APPOINTMENT_STATE_CONFLICT",
                "The appointment outcome conflicts with its current state",
                "business",
            )
        if row.status != "UPCOMING" or row.outcome_at is not None or row.outcome_by is not None:
            raise AppError(
                409,
                "APPOINTMENT_STATE_CONFLICT",
                "The appointment outcome conflicts with its current state",
                "business",
            )
        now = await users.utc_now(db)
        if now <= row.ends_at:
            raise AppError(
                409,
                "APPOINTMENT_NOT_ENDED",
                "The appointment has not ended",
                "business",
            )
        row.status = outcome.status
        row.outcome_at = now
        row.outcome_by = context.user_id
        db.add_all(
            [
                AuditEvent(
                    actor_id=context.user_id,
                    action="APPOINTMENT_OUTCOME_RECORDED",
                    entity_type="APPOINTMENT",
                    entity_id=str(row.id),
                    request_id=request_id,
                    event_metadata={"status": outcome.status},
                ),
                *(
                    Notification(
                        recipient_id=recipient,
                        appointment_id=row.id,
                        event_type="APPOINTMENT_OUTCOME_RECORDED",
                    )
                    for recipient in (row.student_id, row.staff_id)
                ),
            ]
        )
        await db.flush()
        result = StaffAppointmentOutcomeResponse(
            id=row.id, status=outcome.status, outcome_at=UtcTimestamp(now)
        )
        await commit_safely(db, transaction)
        return result
    except BaseException:
        await rollback(db)
        raise


async def record_staff_appointment_outcome(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    outcome: StaffAppointmentOutcomeRequest,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> StaffAppointmentOutcomeResponse:
    """Record an assigned appointment result once its stored end time has passed."""
    if context.role != "STAFF":
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        return await _record_outcome_attempt(
                            db,
                            appointment_id=appointment_id,
                            outcome=outcome,
                            context=context,
                            request_id=request_id,
                            settings=settings,
                        )
                except SQLAlchemyError as error:
                    if (
                        mysql_error_code(error) not in RETRYABLE_MYSQL_ERRORS
                        or attempt >= settings.max_deadlock_retries
                    ):
                        raise ServiceUnavailable() from None
                    remaining = deadline - asyncio.get_running_loop().time()
                    delay = min(0.01 * (2**attempt), max(0.0, remaining))
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
        raise ServiceUnavailable()
    except (TimeoutError, CommitOutcomeUnknown, SQLAlchemyError):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise
