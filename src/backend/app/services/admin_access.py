"""Revalidate administrator access inside a protected transaction."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_error
from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import read_transaction
from app.domain.auth import AuthContext
from app.models import ServiceSetting, User
from app.repositories import appointments, sessions, users


def require_admin(user: User | None) -> User:
    if user is None or user.role != "ADMIN" or not user.is_active:
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    return user


async def lock_active_session(db: AsyncSession, context: AuthContext, settings: Settings) -> None:
    async with asyncio.timeout(settings.db_lock_timeout_seconds):
        session = await sessions.lock_active_by_id(
            db, context.session_id, context.user_id, await users.utc_now(db)
        )
    if session is None:
        raise auth_error("INVALID_SESSION")


async def lock_admin(db: AsyncSession, context: AuthContext, settings: Settings) -> User:
    async with asyncio.timeout(settings.db_lock_timeout_seconds):
        actor = require_admin(await users.lock_by_id(db, context.user_id))
    await lock_active_session(db, context, settings)
    return actor


@asynccontextmanager
async def read_under_policy_lock(
    db: AsyncSession, context: AuthContext, settings: Settings
) -> AsyncIterator[ServiceSetting]:
    async with read_transaction(db, settings):
        async with asyncio.timeout(settings.db_lock_timeout_seconds):
            policy = await appointments.lock_service_settings(db)
        if policy is None:
            raise ServiceUnavailable()
        await lock_admin(db, context, settings)
        yield policy
