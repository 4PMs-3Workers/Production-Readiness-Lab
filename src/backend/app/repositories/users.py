"""Explicit account queries; transaction ownership stays with the caller."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


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


async def utc_now(db: AsyncSession) -> datetime:
    now = await db.scalar(text("SELECT UTC_TIMESTAMP(6)"))
    return now.replace(tzinfo=UTC)
