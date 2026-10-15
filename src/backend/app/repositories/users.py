"""Explicit account queries; transaction ownership stays with the caller."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def find_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def lock_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    return await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def utc_now(db: AsyncSession) -> datetime:
    now = await db.scalar(text("SELECT UTC_TIMESTAMP(6)"))
    return now.replace(tzinfo=UTC)
