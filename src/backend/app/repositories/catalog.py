"""Bounded, read-only database queries for the student catalog."""

from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Purpose, StaffPurpose, User
from app.schemas.catalog import PurposeQuery, StaffQuery


async def find_active_purpose(db: AsyncSession, purpose_id: UUID) -> Purpose | None:
    statement = select(Purpose).where(Purpose.id == purpose_id, Purpose.is_active.is_(True))
    return await db.scalar(statement)


async def find_accepting_staff(db: AsyncSession, purpose_id: UUID, staff_id: UUID) -> UUID | None:
    statement = (
        select(User.id)
        .join(StaffPurpose, StaffPurpose.staff_id == User.id)
        .where(
            User.id == staff_id,
            User.role == "STAFF",
            User.is_active.is_(True),
            StaffPurpose.purpose_id == purpose_id,
            StaffPurpose.is_accepting.is_(True),
        )
    )
    return cast(UUID | None, await db.scalar(statement))


async def read_purpose_page(
    db: AsyncSession, query: PurposeQuery
) -> tuple[tuple[Purpose, ...], int]:
    active = Purpose.is_active.is_(True)
    total = await db.scalar(select(func.count()).select_from(Purpose).where(active))
    statement = (
        select(Purpose)
        .where(active)
        .order_by(Purpose.code.asc(), Purpose.id.asc())
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    )
    rows = tuple((await db.scalars(statement)).all())
    return rows, int(total or 0)


async def read_staff_page(
    db: AsyncSession, query: StaffQuery
) -> tuple[tuple[tuple[UUID, str], ...], int]:
    eligible = (
        User.role == "STAFF",
        User.is_active.is_(True),
        StaffPurpose.purpose_id == query.purpose_id,
        StaffPurpose.is_accepting.is_(True),
    )
    count_statement = (
        select(func.count(User.id))
        .select_from(User)
        .join(StaffPurpose, StaffPurpose.staff_id == User.id)
        .where(*eligible)
    )
    total = await db.scalar(count_statement)
    rows_statement = (
        select(User.id, User.name)
        .join(StaffPurpose, StaffPurpose.staff_id == User.id)
        .where(*eligible)
        .order_by(User.name.asc(), User.id.asc())
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    )
    rows = tuple(
        (cast(UUID, user_id), name) for user_id, name in (await db.execute(rows_statement))
    )
    return rows, int(total or 0)
