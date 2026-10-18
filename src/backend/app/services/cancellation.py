"""Locked, deadline-aware appointment cancellation with atomic private side effects."""

import asyncio
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
from app.schemas.appointments import AppointmentCancelRequest, AppointmentCancelResponse
from app.services.availability_rules import business_day_bounds, validate_policy


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Requested appointment was not found", "input")


async def _attempt(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    payload: AppointmentCancelRequest,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    actor_role: str,
) -> AppointmentCancelResponse:
    transaction = await db.begin()
    try:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            setting = await appointments.lock_service_settings(db)
        if actor_role == "STUDENT":
            identities = await appointments.find_cancellation_accounts(
                db, appointment_id=appointment_id, student_id=context.user_id
            )
        else:
            identities = await appointments.find_staff_cancellation_accounts(
                db, appointment_id=appointment_id, staff_id=context.user_id
            )
        if identities is None:
            raise _not_found()
        locked_users = {}
        for account_id in sorted(
            {identities.student_id, identities.staff_id}, key=lambda x: x.bytes
        ):
            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                locked_users[account_id] = await users.lock_by_id(db, account_id)
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            row = await appointments.lock_appointment(db, appointment_id)
        actor = locked_users.get(context.user_id)
        if (
            row is None
            or actor is None
            or actor.role != actor_role
            or not actor.is_active
            or (actor_role == "STUDENT" and row.student_id != context.user_id)
            or (actor_role == "STAFF" and row.staff_id != context.user_id)
        ):
            raise _not_found()
        if row.staff_id != identities.staff_id:
            # A changed lock identity cannot be safely used within this transaction.
            raise ServiceUnavailable()
        if row.status == "CANCELLED":
            if row.cancelled_at is None:
                raise ServiceUnavailable()
            result = AppointmentCancelResponse(
                id=row.id, status="CANCELLED", cancelled_at=row.cancelled_at
            )
            await rollback(db)
            return result
        if row.status not in appointments.BLOCKING_STATUSES:
            raise AppError(
                409,
                "APPOINTMENT_STATE_CONFLICT",
                "The appointment cannot be cancelled in its current state",
                "business",
            )
        proposal = None
        if row.status == "CHANGE_PENDING":
            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                proposal = await appointments.lock_pending_proposal(db, row.id)
            if proposal is None:
                raise ServiceUnavailable()
        if setting is None:
            raise ServiceUnavailable()
        zone = validate_policy(
            AvailabilityPolicy(
                timezone=setting.timezone,
                start_step_minutes=setting.start_step_minutes,
                booking_horizon_days=setting.booking_horizon_days,
            )
        )
        # The database clock is fetched only after every lock, including pending proposal.
        now = await users.utc_now(db)
        cutoff = business_day_bounds(
            selected_date=row.starts_at.astimezone(zone).date(), zone=zone
        ).starts_at
        if now >= cutoff:
            raise AppError(
                409,
                "CANCELLATION_DEADLINE_PASSED",
                "The appointment cancellation deadline has passed",
                "business",
            )
        row.status = "CANCELLED"
        row.cancelled_at = now
        row.cancelled_by = context.user_id
        row.cancel_reason = payload.reason
        if proposal is not None:
            proposal.status = "CANCELLED"
            proposal.resolved_at = now
            proposal.resolved_by = context.user_id
        db.add_all(
            [
                AuditEvent(
                    actor_id=context.user_id,
                    action="APPOINTMENT_CANCELLED",
                    entity_type="APPOINTMENT",
                    entity_id=str(row.id),
                    request_id=request_id,
                    event_metadata={},
                ),
                *(
                    Notification(
                        recipient_id=recipient,
                        appointment_id=row.id,
                        event_type="APPOINTMENT_CANCELLED",
                    )
                    for recipient in (row.student_id, row.staff_id)
                ),
            ]
        )
        await db.flush()
        result = AppointmentCancelResponse(id=row.id, status="CANCELLED", cancelled_at=now)
        await commit_safely(db, transaction)
        return result
    except BaseException:
        await rollback(db)
        raise


async def cancel_appointment(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    payload: AppointmentCancelRequest,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> AppointmentCancelResponse:
    if context.role != "STUDENT":
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        return await _attempt(
                            db,
                            appointment_id=appointment_id,
                            payload=payload,
                            context=context,
                            request_id=request_id,
                            settings=settings,
                            actor_role="STUDENT",
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
    except (
        TimeoutError,
        CommitOutcomeUnknown,
        AvailabilityConfigurationError,
        BookingDateError,
        SQLAlchemyError,
        OverflowError,
    ):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise


async def cancel_staff_appointment(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    payload: AppointmentCancelRequest,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> AppointmentCancelResponse:
    """Cancel an assigned appointment using the same lock and commit guarantees."""
    if context.role != "STAFF":
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        return await _attempt(
                            db,
                            appointment_id=appointment_id,
                            payload=payload,
                            context=context,
                            request_id=request_id,
                            settings=settings,
                            actor_role="STAFF",
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
    except (
        TimeoutError,
        CommitOutcomeUnknown,
        AvailabilityConfigurationError,
        BookingDateError,
        SQLAlchemyError,
        OverflowError,
    ):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise
