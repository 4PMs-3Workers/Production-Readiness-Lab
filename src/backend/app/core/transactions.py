"""Shared abort and commit-outcome safety for transaction-owning services."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from typing import Any, Protocol, cast

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.core.config import Settings
from app.core.errors import ServiceUnavailable

RETRYABLE_MYSQL_ERRORS = {1205, 1213}
SpanFactory = Callable[[Any], AbstractContextManager[object]]
FailureCallback = Callable[[BaseException], object]


class _SpanLike(Protocol):
    def set_attribute(self, key: str, value: object) -> object: ...


@contextmanager
def _safe_span(span_factory: SpanFactory | None, name: str) -> Iterator[object | None]:
    manager: AbstractContextManager[object] | None = None
    span: object | None = None
    if span_factory is not None:
        try:
            manager = span_factory(name)
            span = manager.__enter__()
        except BaseException:
            manager = None
            span = None
    try:
        yield span
    except BaseException as business_error:
        if manager is not None:
            try:
                manager.__exit__(type(business_error), business_error, business_error.__traceback__)
            except BaseException:
                pass
        raise
    else:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except BaseException:
                pass


def _span_outcome(span: object | None, outcome: str) -> None:
    if span is None:
        return
    try:
        cast(_SpanLike, span).set_attribute("business.outcome", outcome)
    except BaseException:
        pass


def _report_failure(callback: FailureCallback | None, error: BaseException) -> None:
    if callback is None:
        return
    try:
        callback(error)
    except BaseException:
        pass


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


async def rollback(
    db: AsyncSession,
    *,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> None:
    if not db.in_transaction():
        return
    try:
        with _safe_span(span_factory, "db.rollback") as span:
            try:
                await db.rollback()
            except SQLAlchemyError:
                _span_outcome(span, "failed")
                raise
            else:
                _span_outcome(span, "noop")
    except SQLAlchemyError as error:
        _report_failure(failure_callback, error)
        await invalidate(db)


@asynccontextmanager
async def read_transaction(
    db: AsyncSession,
    settings: Settings,
    *,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> AsyncIterator[None]:
    """Bound a read-only transaction and release it before returning or propagating errors."""
    try:
        async with asyncio.timeout(settings.db_query_timeout_seconds):
            transaction = db.begin()
            await transaction.start()
            try:
                yield
            finally:
                if transaction.is_active:
                    with _safe_span(span_factory, "db.rollback") as span:
                        try:
                            await transaction.rollback()
                        except SQLAlchemyError:
                            _span_outcome(span, "failed")
                            raise
                        else:
                            _span_outcome(span, "noop")
    except (SQLAlchemyError, TimeoutError) as error:
        if isinstance(error, SQLAlchemyError):
            _report_failure(failure_callback, error)
        await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise ServiceUnavailable() from None


async def commit_safely(
    db: AsyncSession,
    transaction: AsyncSessionTransaction,
    *,
    known_abort: Callable[[SQLAlchemyError], bool] | None = None,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> None:
    try:
        with _safe_span(span_factory, "db.commit") as span:
            try:
                await transaction.commit()
            except SQLAlchemyError as error:
                known = mysql_error_code(error) in RETRYABLE_MYSQL_ERRORS or (
                    known_abort is not None and known_abort(error)
                )
                _span_outcome(span, "failed" if known else "commit_unknown")
                raise
            except asyncio.CancelledError:
                _span_outcome(span, "commit_unknown")
                raise
            else:
                _span_outcome(span, "committed")
    except SQLAlchemyError as error:
        _report_failure(failure_callback, error)
        await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        if mysql_error_code(error) in RETRYABLE_MYSQL_ERRORS or (
            known_abort is not None and known_abort(error)
        ):
            raise
        await invalidate(db)
        raise CommitOutcomeUnknown() from error
    except asyncio.CancelledError:
        await invalidate(db)
        raise
