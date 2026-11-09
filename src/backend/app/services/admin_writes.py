"""Serialize administrative mutations with booking policy readers."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    mysql_error_code,
    rollback,
)
from app.models import ServiceSetting
from app.repositories import appointments

T = TypeVar("T")


async def write_under_policy_lock(
    db: AsyncSession,
    operation: Callable[[ServiceSetting], Awaitable[tuple[T, bool]]],
    *,
    settings: Settings,
    duplicate_error: AppError | None = None,
    commit: Callable[[AsyncSession, AsyncSessionTransaction], Awaitable[None]] = commit_safely,
) -> T:
    """Retry only known-aborted attempts within the request's deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.request_timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        transaction = await db.begin()
                        try:
                            async with asyncio.timeout(settings.db_lock_timeout_seconds):
                                policy = (
                                    await appointments.lock_service_settings_for_account_change(db)
                                )
                            if policy is None:
                                raise ServiceUnavailable()
                            result, changed = await operation(policy)
                            if changed:
                                await db.flush()
                                await commit(db, transaction)
                            else:
                                await rollback(db)
                            return result
                        except BaseException:
                            await rollback(db)
                            raise
                except SQLAlchemyError as error:
                    await rollback(db)
                    code = mysql_error_code(error)
                    if code == 1062 and duplicate_error is not None:
                        raise duplicate_error from None
                    if (
                        code not in RETRYABLE_MYSQL_ERRORS
                        or attempt >= settings.max_deadlock_retries
                    ):
                        raise ServiceUnavailable() from None
                    delay = min(0.01 * 2**attempt, max(0.0, deadline - loop.time()))
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
            raise ServiceUnavailable()
    except AppError:
        await rollback(db)
        raise
    except (SQLAlchemyError, TimeoutError, CommitOutcomeUnknown):
        await rollback(db)
        raise ServiceUnavailable() from None
    except asyncio.CancelledError:
        await rollback(db)
        raise
