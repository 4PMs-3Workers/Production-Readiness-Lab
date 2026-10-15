"""Session storage never receives raw bearer tokens."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session, User


async def find_with_user(db: AsyncSession, token_hash: bytes) -> tuple[Session, User] | None:
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == token_hash)
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


async def add_session(db: AsyncSession, session: Session) -> None:
    db.add(session)
    await db.flush()


async def revoke_one(db: AsyncSession, session_id: UUID, user_id: UUID, now: datetime) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_all(db: AsyncSession, user_id: UUID, now: datetime) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )
