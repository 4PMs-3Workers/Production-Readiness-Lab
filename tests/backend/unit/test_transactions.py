"""Narrow shared-helper contracts; database races live in MySQL integration tests."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.core.transactions import CommitOutcomeUnknown, commit_safely, mysql_error_code, rollback


@pytest.mark.parametrize(
    "original,expected",
    [
        (Exception(1213, "deadlock"), 1213),
        (Exception("1213", "not an integer"), None),
        (Exception(), None),
    ],
)
def test_mysql_error_code_uses_integer_driver_argument(original, expected):
    assert mysql_error_code(OperationalError("sql", {}, original)) == expected


def test_mysql_error_code_prefers_errno_and_handles_non_driver_error():
    class DriverError(Exception):
        errno = 1205

    original = DriverError(2013, "connection")
    assert mysql_error_code(OperationalError("sql", {}, original)) == 1205
    assert mysql_error_code(SQLAlchemyError("plain")) is None


@pytest.mark.parametrize("active", [False, True])
async def test_rollback_only_aborts_active_transactions(active):
    db = Mock(in_transaction=Mock(return_value=active), rollback=AsyncMock())
    await rollback(db)
    assert db.rollback.await_count == int(active)


async def test_failed_rollback_invalidates_and_failed_invalidation_is_contained():
    db = Mock(
        in_transaction=Mock(return_value=True),
        rollback=AsyncMock(side_effect=SQLAlchemyError("rollback failed")),
        invalidate=AsyncMock(side_effect=SQLAlchemyError("invalidate failed")),
    )
    await rollback(db)
    db.rollback.assert_awaited_once()
    db.invalidate.assert_awaited_once()


@pytest.mark.parametrize("code", [1205, 1213, 2013])
async def test_commit_distinguishes_known_retryable_abort_from_unknown_outcome(code):
    error = OperationalError("COMMIT", {}, Exception(code, "driver detail"))
    db = Mock(in_transaction=Mock(return_value=True), rollback=AsyncMock(), invalidate=AsyncMock())
    transaction = Mock(commit=AsyncMock(side_effect=error))
    with pytest.raises(OperationalError if code in {1205, 1213} else CommitOutcomeUnknown):
        await commit_safely(db, transaction)
    transaction.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()
    assert db.invalidate.await_count == int(code == 2013)


async def test_named_known_abort_stays_available_to_booking_replay_recovery():
    error = IntegrityError("COMMIT", {}, Exception(1062, "named duplicate"))
    db = Mock(in_transaction=Mock(return_value=True), rollback=AsyncMock(), invalidate=AsyncMock())
    transaction = Mock(commit=AsyncMock(side_effect=error))
    with pytest.raises(IntegrityError) as caught:
        await commit_safely(db, transaction, known_abort=lambda candidate: candidate is error)
    assert caught.value is error
    db.invalidate.assert_not_awaited()


async def test_cancellation_during_commit_invalidates_and_propagates():
    db = Mock(invalidate=AsyncMock())
    transaction = Mock(commit=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await commit_safely(db, transaction)
    db.invalidate.assert_awaited_once()


async def test_successful_commit_does_not_abort_or_invalidate():
    db = Mock(rollback=AsyncMock(), invalidate=AsyncMock())
    transaction = Mock(commit=AsyncMock())
    await commit_safely(db, transaction)
    transaction.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    db.invalidate.assert_not_awaited()
