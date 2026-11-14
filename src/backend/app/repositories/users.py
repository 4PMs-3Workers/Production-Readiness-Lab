"""Explicit account queries; transaction ownership stays with the caller."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    ChangeProposal,
    StaffPurpose,
    StaffUnavailability,
    User,
)
from app.schemas.admin_accounts import AccountQuery


async def find_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def email_throttle_identity(db: AsyncSession, email: str) -> bytes:
    """Return a non-reversible key matching MySQL's account email collation."""
    weight = await db.scalar(
        text("SELECT WEIGHT_STRING(CONVERT(:email USING utf8mb4) COLLATE utf8mb4_0900_as_ci)"),
        {"email": email},
    )
    if not isinstance(weight, (bytes, bytearray, memoryview)):
        raise SQLAlchemyError("Could not derive email throttle identity")
    return hashlib.sha256(bytes(weight)).digest()


async def lock_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    return await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def find_student_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    return await db.scalar(
        select(User).where(User.id == user_id, User.role == "STUDENT", User.is_active.is_(True))
    )


async def read_account_page(db: AsyncSession, query: AccountQuery) -> tuple[list[User], int]:
    filters = []
    if query.role is not None:
        filters.append(User.role == query.role)
    if query.is_active is not None:
        filters.append(User.is_active.is_(query.is_active))
    if not query.include_deleted:
        filters.append(User.deleted_at.is_(None))
    if query.search is not None:
        search = query.search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{search}%"
        filters.append(
            or_(User.name.ilike(pattern, escape="\\"), User.email.like(pattern, escape="\\"))
        )
    total = int(await db.scalar(select(func.count()).select_from(User).where(*filters)) or 0)
    offset = (query.page - 1) * query.page_size
    rows = list(
        await db.scalars(
            select(User)
            .where(*filters)
            .order_by(User.email, User.id)
            .limit(query.page_size)
            .offset(offset)
        )
    )
    return rows, total


async def add_account(db: AsyncSession, user: User) -> None:
    db.add(user)
    await db.flush()


async def count_active_role(db: AsyncSession, role: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == role, User.is_active.is_(True))
        )
        or 0
    )


async def has_role_references(db: AsyncSession, user_id: UUID) -> bool:
    references = or_(
        exists().where(
            or_(
                Appointment.student_id == user_id,
                Appointment.staff_id == user_id,
                Appointment.cancelled_by == user_id,
                Appointment.outcome_by == user_id,
            )
        ),
        exists().where(StaffPurpose.staff_id == user_id),
        exists().where(StaffUnavailability.staff_id == user_id),
        exists().where(
            or_(ChangeProposal.created_by == user_id, ChangeProposal.resolved_by == user_id)
        ),
    )
    return bool(await db.scalar(select(references)))


async def has_occupying_staff_appointments(db: AsyncSession, staff_id: UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    Appointment.staff_id == staff_id,
                    Appointment.status.in_(("UPCOMING", "CHANGE_PENDING")),
                )
            )
        )
    )


async def utc_now(db: AsyncSession) -> datetime:
    now = await db.scalar(text("SELECT UTC_TIMESTAMP(6)"))
    return now.replace(tzinfo=UTC)
