"""Transaction-owning student appointment creation with exact-key idempotency."""

import asyncio
import re
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS as _RETRYABLE_MYSQL_ERRORS,
)
from app.core.transactions import (
    CommitOutcomeUnknown as _CommitOutcomeUnknown,
)
from app.core.transactions import (
    CommitUnknownCallback,
    FailureCallback,
    commit_safely,
    report_commit_unknown,
)
from app.core.transactions import (
    mysql_error_code as _mysql_error_code,
)
from app.core.transactions import (
    rollback as _rollback,
)
from app.domain.auth import AuthContext
from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
)
from app.domain.booking import booking_request_hash, require_description
from app.models import Appointment, AuditEvent, Notification, User
from app.observability.tracing import SpanFactory, safe_span, set_business_outcome
from app.repositories import appointments, availability, catalog, users
from app.schemas.appointments import AppointmentCreateRequest, AppointmentCreateResponse
from app.services.availability_rules import (
    business_day_bounds,
    generate_slots,
    validate_booking_date,
    validate_policy,
)

_IDEMPOTENCY_CONSTRAINT = "uq_appointments_creation_request"
_CONSTRAINT_KEY = re.compile(r"for key\s+(['\"`])([^'\"`]+)\1\s*$", re.IGNORECASE)


class _NamedDuplicate(Exception):
    """The exact student/idempotency-key unique constraint rejected this insert."""


def _is_named_creation_duplicate(error: SQLAlchemyError) -> bool:
    if _mysql_error_code(error) != 1062:
        return False
    original = getattr(error, "orig", None)
    # str(DBAPIError) formats args as a tuple and can escape the constraint quotes
    # when a binary UUID/key contains quotes. Parse the driver's raw message instead.
    args = getattr(original, "args", ())
    message = args[1] if len(args) > 1 and isinstance(args[1], str) else str(original or "")
    match = _CONSTRAINT_KEY.search(message)
    return bool(match and match.group(2).rsplit(".", 1)[-1] == _IDEMPOTENCY_CONSTRAINT)


def _receipt(row: Appointment) -> AppointmentCreateResponse:
    return AppointmentCreateResponse(id=row.id, status="UPCOMING", created_at=row.created_at)


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")


def _booking_conflict() -> AppError:
    return AppError(
        409,
        "BOOKING_CONFLICT",
        "The requested appointment slot is no longer available",
        "business",
    )


def _quota_conflict() -> AppError:
    return AppError(
        409,
        "BOOKING_QUOTA_EXCEEDED",
        "The maximum number of upcoming appointments has been reached",
        "business",
    )


def _idempotency_conflict() -> AppError:
    return AppError(
        409,
        "IDEMPOTENCY_CONFLICT",
        "This Idempotency-Key was already used for different booking details",
        "business",
    )


def _resolve_replay(
    row: Appointment | None, *, expected_hash: bytes
) -> tuple[AppointmentCreateResponse, bool] | None:
    if row is None:
        return None
    if row.creation_request_hash != expected_hash:
        raise _idempotency_conflict()
    return _receipt(row), True


async def _read_committed_key(
    db: AsyncSession,
    *,
    student_id: UUID,
    key: str,
    expected_hash: bytes,
    settings: Settings,
) -> tuple[AppointmentCreateResponse, bool] | None:
    async with asyncio.timeout(settings.db_query_timeout_seconds):
        async with db.begin():
            row = await appointments.find_creation_request(db, student_id=student_id, key=key)
            return _resolve_replay(row, expected_hash=expected_hash)


async def _run_attempt_with_commit_safety(
    db: AsyncSession,
    payload: AppointmentCreateRequest,
    *,
    context: AuthContext,
    idempotency_key: str,
    request_id: str,
    request_hash: bytes,
    settings: Settings,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
    commit_unknown_callback: CommitUnknownCallback | None = None,
) -> tuple[AppointmentCreateResponse, bool]:
    """Run one complete transaction and distinguish deterministic aborts from uncertain commit."""
    # The low-level attempt owns rollback for statement errors. A commit-time transport failure
    # can leave the server outcome unknown, so invalidate the connection and never replay writes.
    transaction = await db.begin()
    try:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            setting = await appointments.lock_service_settings(db)
        if setting is None:
            raise ServiceUnavailable()
        account_ids = sorted({context.user_id, payload.staff_id}, key=lambda value: value.bytes)
        locked_users: dict[UUID, User | None] = {}
        for account_id in account_ids:
            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                locked_users[account_id] = await users.lock_by_id(db, account_id)

        existing = await appointments.find_creation_request(
            db, student_id=context.user_id, key=idempotency_key
        )
        replay = _resolve_replay(existing, expected_hash=request_hash)
        if replay is not None:
            with safe_span(span_factory, "db.commit") as span:
                try:
                    await transaction.commit()
                except asyncio.CancelledError:
                    set_business_outcome(span, "commit_unknown")
                    report_commit_unknown(commit_unknown_callback)
                    raise
                except BaseException:
                    set_business_outcome(span, "failed")
                    raise
                else:
                    set_business_outcome(span, "replayed")
            return replay

        student = locked_users.get(context.user_id)
        staff = locked_users.get(payload.staff_id)
        if (
            student is None
            or student.role != "STUDENT"
            or not student.is_active
            or staff is None
            or staff.role != "STAFF"
            or not staff.is_active
        ):
            raise _not_found()

        # This separate SQL read deliberately follows every lock and is the sole booking clock.
        now = await users.utc_now(db)
        policy = AvailabilityPolicy(
            timezone=setting.timezone,
            start_step_minutes=setting.start_step_minutes,
            booking_horizon_days=setting.booking_horizon_days,
        )
        zone = validate_policy(policy)
        try:
            starts_at = payload.starts_at.astimezone(UTC)
            selected_date = starts_at.astimezone(zone).date()
        except OverflowError:
            raise _booking_conflict() from None
        validate_booking_date(selected_date=selected_date, now=now, policy=policy)

        purpose = await catalog.find_active_purpose(db, payload.purpose_id)
        if purpose is None:
            raise _not_found()
        eligible_staff = await catalog.find_accepting_staff(
            db, payload.purpose_id, payload.staff_id
        )
        if eligible_staff is None:
            raise _not_found()
        try:
            description = require_description(
                payload.description, required=purpose.requires_free_text
            )
        except ValueError:
            raise AppError(422, "VALIDATION_ERROR", "Invalid request", "input") from None

        if await appointments.count_blocking_for_student(db, student_id=context.user_id) >= 3:
            raise _quota_conflict()

        bounds = business_day_bounds(selected_date=selected_date, zone=zone)
        windows = await availability.read_windows(db, selected_date.isoweekday())
        unavailability = await availability.read_unavailability(
            db, staff_id=payload.staff_id, bounds=bounds
        )
        staff_busy = await availability.read_staff_appointments(
            db, staff_id=payload.staff_id, bounds=bounds
        )
        student_busy = await availability.read_student_appointments(
            db, student_id=context.user_id, bounds=bounds
        )
        slots = generate_slots(
            selected_date=selected_date,
            now=now,
            policy=policy,
            duration_minutes=purpose.duration_minutes,
            windows=windows,
            busy=(*unavailability, *staff_busy, *student_busy),
        )
        chosen_slot = next((slot for slot in slots if slot.starts_at == starts_at), None)
        if chosen_slot is None:
            raise _booking_conflict()
        ends_at = starts_at + timedelta(minutes=purpose.duration_minutes)
        if chosen_slot.ends_at != ends_at:
            raise ServiceUnavailable()

        appointment = Appointment(
            id=uuid4(),
            student_id=context.user_id,
            staff_id=payload.staff_id,
            purpose_id=payload.purpose_id,
            duration_minutes=purpose.duration_minutes,
            other_reason=description,
            starts_at=starts_at,
            ends_at=ends_at,
            status="UPCOMING",
            creation_request_key=idempotency_key,
            creation_request_hash=request_hash,
        )
        audit = AuditEvent(
            actor_id=context.user_id,
            action="APPOINTMENT_CREATED",
            entity_type="APPOINTMENT",
            entity_id=str(appointment.id),
            request_id=request_id,
            event_metadata={
                "purpose_id": str(payload.purpose_id),
                "staff_id": str(payload.staff_id),
                "duration_minutes": purpose.duration_minutes,
            },
        )
        notification_rows = (
            Notification(
                recipient_id=context.user_id,
                appointment_id=appointment.id,
                event_type="APPOINTMENT_CREATED",
            ),
            Notification(
                recipient_id=payload.staff_id,
                appointment_id=appointment.id,
                event_type="APPOINTMENT_CREATED",
            ),
        )
        await appointments.add_creation_bundle(
            db,
            appointment=appointment,
            audit_event=audit,
            notifications=notification_rows,
        )
        await db.flush()
        result = (_receipt(appointment), False)
        await commit_safely(
            db,
            transaction,
            known_abort=_is_named_creation_duplicate,
            span_factory=span_factory,
            failure_callback=failure_callback,
            commit_unknown_callback=commit_unknown_callback,
        )
        return result
    except SQLAlchemyError as error:
        if failure_callback is not None:
            try:
                failure_callback(error)
            except BaseException:
                pass
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        if _is_named_creation_duplicate(error):
            raise _NamedDuplicate() from error
        if _mysql_error_code(error) in _RETRYABLE_MYSQL_ERRORS:
            raise
        raise
    except asyncio.CancelledError:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise
    except BaseException:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise


async def _create_appointment_impl(
    db: AsyncSession,
    payload: AppointmentCreateRequest,
    *,
    context: AuthContext,
    idempotency_key: str,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
    commit_unknown_callback: CommitUnknownCallback | None = None,
) -> tuple[AppointmentCreateResponse, bool]:
    """Create one appointment transactionally, returning the immutable receipt and replay flag.

    ``deadline`` is an absolute event-loop deadline shared with the HTTP request. All retries
    spend from that same budget; an uncertain commit is never retried automatically.
    """
    if context.role != "STUDENT":
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")

    request_hash = booking_request_hash(payload)
    try:
        async with asyncio.timeout_at(deadline):
            try:
                replay = await _read_committed_key(
                    db,
                    student_id=context.user_id,
                    key=idempotency_key,
                    expected_hash=request_hash,
                    settings=settings,
                )
            except AppError:
                raise
            except SQLAlchemyError as error:
                if failure_callback is not None:
                    try:
                        failure_callback(error)
                    except BaseException:
                        pass
                await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
                raise ServiceUnavailable() from None
            if replay is not None:
                return replay

            retries = settings.max_deadlock_retries
            for attempt in range(retries + 1):
                try:
                    # Match the existing services' transaction query budget. The outer
                    # request deadline also caps every attempt, lookup, lock, and retry.
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        return await _run_attempt_with_commit_safety(
                            db,
                            payload,
                            context=context,
                            idempotency_key=idempotency_key,
                            request_id=request_id,
                            request_hash=request_hash,
                            settings=settings,
                            span_factory=span_factory,
                            failure_callback=failure_callback,
                            commit_unknown_callback=commit_unknown_callback,
                        )
                except _NamedDuplicate:
                    try:
                        replay = await _read_committed_key(
                            db,
                            student_id=context.user_id,
                            key=idempotency_key,
                            expected_hash=request_hash,
                            settings=settings,
                        )
                    except AppError:
                        raise
                    except SQLAlchemyError as error:
                        if failure_callback is not None:
                            try:
                                failure_callback(error)
                            except BaseException:
                                pass
                        await _rollback(
                            db, span_factory=span_factory, failure_callback=failure_callback
                        )
                        raise ServiceUnavailable() from None
                    if replay is not None:
                        return replay
                    raise ServiceUnavailable() from None
                except _CommitOutcomeUnknown:
                    raise
                except SQLAlchemyError as error:
                    if failure_callback is not None:
                        try:
                            failure_callback(error)
                        except BaseException:
                            pass
                    await _rollback(
                        db, span_factory=span_factory, failure_callback=failure_callback
                    )
                    if _mysql_error_code(error) not in _RETRYABLE_MYSQL_ERRORS:
                        raise ServiceUnavailable() from None
                    if attempt >= retries:
                        raise ServiceUnavailable() from None
                    remaining = deadline - asyncio.get_running_loop().time()
                    delay = min(0.01 * (2**attempt), max(0.0, remaining))
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
                except AppError:
                    await _rollback(
                        db, span_factory=span_factory, failure_callback=failure_callback
                    )
                    raise
            raise ServiceUnavailable() from None
    except TimeoutError:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise
    except AvailabilityConfigurationError:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise ServiceUnavailable() from None
    except BookingDateError:
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise _booking_conflict() from None
    except SQLAlchemyError as error:
        if failure_callback is not None:
            try:
                failure_callback(error)
            except BaseException:
                pass
        await _rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise ServiceUnavailable() from None


async def create_appointment(
    db: AsyncSession,
    payload: AppointmentCreateRequest,
    *,
    context: AuthContext,
    idempotency_key: str,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> tuple[AppointmentCreateResponse, bool]:
    """Create one booking with optional owner-injected, failure-safe tracing."""
    with safe_span(span_factory, "booking.create") as span:
        commit_unknown = False

        def mark_commit_unknown() -> None:
            nonlocal commit_unknown
            commit_unknown = True
            set_business_outcome(span, "commit_unknown")

        try:
            result = await _create_appointment_impl(
                db,
                payload,
                context=context,
                idempotency_key=idempotency_key,
                request_id=request_id,
                settings=settings,
                deadline=deadline,
                span_factory=span_factory,
                failure_callback=failure_callback,
                commit_unknown_callback=mark_commit_unknown,
            )
        except _CommitOutcomeUnknown:
            set_business_outcome(span, "commit_unknown")
            raise ServiceUnavailable() from None
        except BaseException:
            if not commit_unknown:
                set_business_outcome(span, "failed")
            raise
        else:
            set_business_outcome(span, "replayed" if result[1] else "committed")
            return result
