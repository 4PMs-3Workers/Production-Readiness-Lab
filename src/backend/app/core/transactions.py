"""Shared abort and commit-outcome safety for transaction-owning services."""

import asyncio
from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

RETRYABLE_MYSQL_ERRORS = {1205, 1213}


class CommitOutcomeUnknown(Exception):
    """The connection failed during commit; writes must never be auto-retried."""


def mysql_error_code(error: SQLAlchemyError) -> int | None:
    original = getattr(error, "orig", None)
    if original is None:
        return None
    code = getattr(original, "errno", None)
    if isinstance(code, int):
        return code
    args = getattr(original, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


async def invalidate(db: AsyncSession) -> None:
    try:
        await db.invalidate()
    except SQLAlchemyError:
        pass


async def rollback(db: AsyncSession) -> None:
    if not db.in_transaction():
        return
    try:
        await db.rollback()
    except SQLAlchemyError:
        await invalidate(db)


async def commit_safely(
    db: AsyncSession,
    transaction: AsyncSessionTransaction,
    *,
    known_abort: Callable[[SQLAlchemyError], bool] | None = None,
) -> None:
    try:
        await transaction.commit()
    except SQLAlchemyError as error:
        await rollback(db)
        if mysql_error_code(error) in RETRYABLE_MYSQL_ERRORS or (
            known_abort is not None and known_abort(error)
        ):
            raise
        await invalidate(db)
        raise CommitOutcomeUnknown() from error
    except asyncio.CancelledError:
        await invalidate(db)
        raise
