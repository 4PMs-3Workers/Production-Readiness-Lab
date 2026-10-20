"""Staff-owned configuration queries; the caller owns locks and transactions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Purpose, StaffPurpose, StaffUnavailability
from app.repositories.appointments import BLOCKING_STATUSES
from app.schemas.staff_availability import (
    StaffPurposeQuery,
    StaffPurposeResponse,
    UnavailabilityQuery,
)


async def read_purposes(
    db: AsyncSession, *, staff_id: UUID, query: StaffPurposeQuery
) -> tuple[list[StaffPurposeResponse], int]:
    join = and_(StaffPurpose.staff_id == staff_id, StaffPurpose.purpose_id == Purpose.id)
    visible = or_(Purpose.is_active.is_(True), StaffPurpose.staff_id.is_not(None))
    total = int(
        await db.scalar(
            select(func.count()).select_from(Purpose).outerjoin(StaffPurpose, join).where(visible)
        )
        or 0
    )
    offset = (query.page - 1) * query.page_size
    if offset >= total:
        return [], total
    rows = (
        await db.execute(
            select(Purpose, func.coalesce(StaffPurpose.is_accepting, False))
            .outerjoin(StaffPurpose, join)
            .where(visible)
            .order_by(Purpose.name, Purpose.id)
            .offset(offset)
            .limit(query.page_size)
        )
    ).all()
    return [
        StaffPurposeResponse(
            id=purpose.id,
            code=purpose.code,
            name=purpose.name,
            duration_minutes=purpose.duration_minutes,
            requires_free_text=purpose.requires_free_text,
            is_active=purpose.is_active,
            is_accepting=bool(accepting),
        )
        for purpose, accepting in rows
    ], total


async def lock_purpose(db: AsyncSession, purpose_id: UUID) -> Purpose | None:
    return await db.scalar(
        select(Purpose)
        .where(Purpose.id == purpose_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )


async def lock_association(
    db: AsyncSession, *, staff_id: UUID, purpose_id: UUID
) -> StaffPurpose | None:
    return await db.scalar(
        select(StaffPurpose)
        .where(StaffPurpose.staff_id == staff_id, StaffPurpose.purpose_id == purpose_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def read_periods(
    db: AsyncSession, *, staff_id: UUID, query: UnavailabilityQuery
) -> tuple[list[StaffUnavailability], int]:
    filters = [StaffUnavailability.staff_id == staff_id]
    if query.from_at is not None:
        filters.append(StaffUnavailability.ends_at > query.from_at)
    if query.to_at is not None:
        filters.append(StaffUnavailability.starts_at < query.to_at)
    total = int(
        await db.scalar(select(func.count()).select_from(StaffUnavailability).where(*filters)) or 0
    )
    offset = (query.page - 1) * query.page_size
    if offset >= total:
        return [], total
    rows = await db.scalars(
        select(StaffUnavailability)
        .where(*filters)
        .order_by(StaffUnavailability.starts_at, StaffUnavailability.id)
        .offset(offset)
        .limit(query.page_size)
    )
    return list(rows.all()), total


async def lock_exact_period(
    db: AsyncSession, *, staff_id: UUID, starts_at: datetime, ends_at: datetime
) -> StaffUnavailability | None:
    # Staff lock serializes absence checks and insertion, including an empty result.
    return await db.scalar(
        select(StaffUnavailability)
        .where(
            StaffUnavailability.staff_id == staff_id,
            StaffUnavailability.starts_at == starts_at,
            StaffUnavailability.ends_at == ends_at,
        )
        .order_by(StaffUnavailability.id)
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_owned_period(
    db: AsyncSession, *, staff_id: UUID, identifier: UUID
) -> StaffUnavailability | None:
    return await db.scalar(
        select(StaffUnavailability)
        .where(StaffUnavailability.id == identifier, StaffUnavailability.staff_id == staff_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def has_occupying_appointment(
    db: AsyncSession, *, staff_id: UUID, starts_at: datetime, ends_at: datetime
) -> bool:
    # Read after the staff lock under READ COMMITTED, shared with every booking/transition.
    return (
        await db.scalar(
            select(Appointment.id)
            .where(
                Appointment.staff_id == staff_id,
                Appointment.status.in_(BLOCKING_STATUSES),
                Appointment.starts_at < ends_at,
                Appointment.ends_at > starts_at,
            )
            .limit(1)
        )
        is not None
    )
