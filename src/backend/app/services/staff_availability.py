"""Atomic staff configuration writes serialized with appointment booking."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, BusinessConflict, ServiceUnavailable
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    mysql_error_code,
    read_transaction,
    rollback,
)
from app.domain.auth import AuthContext
from app.models import AuditEvent, Purpose, StaffPurpose, StaffUnavailability
from app.repositories import appointments, users
from app.repositories import staff_availability as repository
from app.schemas.common import PageResponse
from app.schemas.staff_availability import (
    StaffPurposeQuery,
    StaffPurposeRequest,
    StaffPurposeResponse,
    UnavailabilityCreateRequest,
    UnavailabilityQuery,
    UnavailabilityResponse,
)

T = TypeVar("T")


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Requested staff configuration was not found", "input")


def _purpose_response(purpose: Purpose, accepting: bool) -> StaffPurposeResponse:
    return StaffPurposeResponse(
        id=purpose.id,
        code=purpose.code,
        name=purpose.name,
        duration_minutes=purpose.duration_minutes,
        requires_free_text=purpose.requires_free_text,
        is_active=purpose.is_active,
        is_accepting=accepting,
    )


def _period_response(period: StaffUnavailability) -> UnavailabilityResponse:
    return UnavailabilityResponse.model_validate(
        {key: getattr(period, key) for key in UnavailabilityResponse.model_fields}
    )


async def list_purposes(
    db: AsyncSession, query: StaffPurposeQuery, *, staff_id: UUID, settings: Settings
) -> PageResponse[StaffPurposeResponse]:
    async with read_transaction(db, settings):
        items, total = await repository.read_purposes(db, staff_id=staff_id, query=query)
    return PageResponse(items=items, total=total, page=query.page, page_size=query.page_size)


async def list_periods(
    db: AsyncSession, query: UnavailabilityQuery, *, staff_id: UUID, settings: Settings
) -> PageResponse[UnavailabilityResponse]:
    async with read_transaction(db, settings):
        rows, total = await repository.read_periods(db, staff_id=staff_id, query=query)
        items = [_period_response(row) for row in rows]
    return PageResponse(items=items, total=total, page=query.page, page_size=query.page_size)


async def _write(
    db: AsyncSession,
    operation: Callable[[], Awaitable[tuple[T, bool]]],
    *,
    context: AuthContext,
    settings: Settings,
    deadline: float,
) -> T:
    """Retry only known-aborted transactions; never replay an uncertain commit."""
    if context.role != "STAFF":
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        transaction = await db.begin()
                        try:
                            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                                policy = await appointments.lock_service_settings(db)
                            if policy is None:
                                raise ServiceUnavailable()
                            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                                actor = await users.lock_by_id(db, context.user_id)
                            if actor is None or actor.role != "STAFF" or not actor.is_active:
                                raise AppError(
                                    403,
                                    "FORBIDDEN_ROLE",
                                    "This operation is not allowed",
                                    "business",
                                )
                            result, changed = await operation()
                            if changed:
                                await db.flush()
                                await commit_safely(db, transaction)
                            else:
                                await rollback(db)
                            return result
                        except BaseException:
                            await rollback(db)
                            raise
                except SQLAlchemyError as error:
                    if (
                        mysql_error_code(error) not in RETRYABLE_MYSQL_ERRORS
                        or attempt >= settings.max_deadlock_retries
                    ):
                        raise ServiceUnavailable() from None
                    delay = min(
                        0.01 * 2**attempt, max(0.0, deadline - asyncio.get_running_loop().time())
                    )
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
        raise ServiceUnavailable()
    except (SQLAlchemyError, TimeoutError, CommitOutcomeUnknown):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise


async def set_purpose(
    db: AsyncSession,
    purpose_id: UUID,
    payload: StaffPurposeRequest,
    *,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> StaffPurposeResponse:
    async def operation() -> tuple[StaffPurposeResponse, bool]:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            purpose = await repository.lock_purpose(db, purpose_id)
            association = await repository.lock_association(
                db, staff_id=context.user_id, purpose_id=purpose_id
            )
        if purpose is None:
            raise _not_found()
        if not purpose.is_active and (association is None or payload.is_accepting):
            raise BusinessConflict("PURPOSE_INACTIVE", "The consultation purpose is inactive")
        if association is not None and association.is_accepting == payload.is_accepting:
            return _purpose_response(purpose, association.is_accepting), False
        if association is None:
            association = StaffPurpose(
                staff_id=context.user_id, purpose_id=purpose_id, is_accepting=payload.is_accepting
            )
            db.add(association)
        else:
            association.is_accepting = payload.is_accepting
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="STAFF_PURPOSE_SET",
                entity_type="STAFF_PURPOSE",
                entity_id=f"{context.user_id}:{purpose_id}",
                request_id=request_id,
                event_metadata={
                    "staff_id": str(context.user_id),
                    "purpose_id": str(purpose_id),
                    "is_accepting": payload.is_accepting,
                },
            )
        )
        return _purpose_response(purpose, payload.is_accepting), True

    return await _write(db, operation, context=context, settings=settings, deadline=deadline)


async def create_period(
    db: AsyncSession,
    payload: UnavailabilityCreateRequest,
    *,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> tuple[UnavailabilityResponse, bool]:
    async def operation() -> tuple[tuple[UnavailabilityResponse, bool], bool]:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            existing = await repository.lock_exact_period(
                db, staff_id=context.user_id, starts_at=payload.starts_at, ends_at=payload.ends_at
            )
        if existing is not None:
            return (_period_response(existing), True), False
        if await repository.has_occupying_appointment(
            db, staff_id=context.user_id, starts_at=payload.starts_at, ends_at=payload.ends_at
        ):
            raise BusinessConflict(
                "UNAVAILABILITY_CONFLICT", "The period overlaps an assigned occupying appointment"
            )
        period = StaffUnavailability(
            staff_id=context.user_id, starts_at=payload.starts_at, ends_at=payload.ends_at
        )
        db.add(period)
        await db.flush()
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="STAFF_UNAVAILABILITY_CREATED",
                entity_type="STAFF_UNAVAILABILITY",
                entity_id=str(period.id),
                request_id=request_id,
                event_metadata={
                    "staff_id": str(context.user_id),
                    "starts_at": payload.starts_at.isoformat(),
                    "ends_at": payload.ends_at.isoformat(),
                },
            )
        )
        return (_period_response(period), False), True

    return await _write(db, operation, context=context, settings=settings, deadline=deadline)


async def delete_period(
    db: AsyncSession,
    identifier: UUID,
    *,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
) -> None:
    async def operation() -> tuple[None, bool]:
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            period = await repository.lock_owned_period(
                db, staff_id=context.user_id, identifier=identifier
            )
        if period is None:
            raise _not_found()
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="STAFF_UNAVAILABILITY_DELETED",
                entity_type="STAFF_UNAVAILABILITY",
                entity_id=str(period.id),
                request_id=request_id,
                event_metadata={
                    "staff_id": str(context.user_id),
                    "starts_at": period.starts_at.isoformat(),
                    "ends_at": period.ends_at.isoformat(),
                },
            )
        )
        await db.delete(period)
        return None, True

    await _write(db, operation, context=context, settings=settings, deadline=deadline)
