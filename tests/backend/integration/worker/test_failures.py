import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_cancellation_concurrency import wait_for_lock
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.models import Appointment, ChangeProposal, ServiceSetting
from app.repositories import users
from app.worker.models import FailureReason, Outcome
from app.worker.processing import _finish_session, process_candidate

pytestmark = pytest.mark.mysql


async def assert_locks_released(env):
    async with asyncio.timeout(2):
        async with env.factory.begin() as db:
            await db.get(ServiceSetting, 1, with_for_update=True)
            for identifier in sorted([env.student_id, env.staff_id], key=lambda value: value.bytes):
                await users.lock_by_id(db, identifier)
            await db.scalar(select(Appointment).with_for_update())
            await db.scalar(select(ChangeProposal).with_for_update())


async def test_audit_failure_rolls_back_proposal_appointment_and_notifications(worker_env):
    env = worker_env
    identifier, appointment_id = await add_proposal(env)

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events"):
            raise OperationalError("private-sql", {}, Exception(1644, "private-payload"))

    event.listen(env.engine.sync_engine, "before_cursor_execute", fail)
    try:
        result = await process_candidate(
            env.factory, identifier, env.settings, "worker.audit-failure"
        )
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", fail)
    assert result.outcome == Outcome.FAILED and result.reason == FailureReason.DATABASE_UNAVAILABLE
    assert await side_effect_counts(env) == (0, 0)
    async with env.factory() as db:
        proposal, row = await db.get(ChangeProposal, identifier), await db.get(
            Appointment, appointment_id
        )
        assert proposal and row
        assert proposal.status == "PENDING" and row.status == "CHANGE_PENDING"
        assert proposal.resolved_at is None and row.cancelled_at is None
    await assert_locks_released(env)


@pytest.mark.parametrize("committed", [True, False])
async def test_unknown_commit_is_never_replayed_and_later_processing_reads_durable_state(
    worker_env, monkeypatch, committed
):
    env = worker_env
    identifier, _ = await add_proposal(env)
    original_commit = AsyncSessionTransaction.commit
    attempts = []
    async with env.factory() as session:

        async def uncertain(transaction):
            if transaction.session is not session:
                return await original_commit(transaction)
            attempts.append(transaction)
            if committed:
                await original_commit(transaction)
            raise OperationalError("COMMIT", {}, Exception(2013, "private-connection-detail"))

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSessionTransaction, "commit", uncertain)
            result = await process_candidate(
                lambda: session, identifier, env.settings, "worker.unknown"
            )
        assert result.reason == FailureReason.COMMIT_UNKNOWN and result.attempts == 1
        assert len(attempts) == 1 and not session.in_transaction()
    assert await side_effect_counts(env) == ((1, 2) if committed else (0, 0))
    await assert_locks_released(env)
    recovered = await process_candidate(env.factory, identifier, env.settings, "worker.recovery")
    assert recovered.outcome == (Outcome.NOOP if committed else Outcome.EXPIRED)
    assert await side_effect_counts(env) == (1, 2)


@pytest.mark.parametrize("code", [1205, 1213])
async def test_known_aborted_lock_failures_retry_in_fresh_transactions(
    worker_env, monkeypatch, code
):
    env = worker_env
    identifier, _ = await add_proposal(env)
    original = users.lock_by_id
    calls = []
    sessions = []

    def factory():
        session = env.factory()
        sessions.append(session)
        return session

    async def transient(db, account_id):
        calls.append(account_id)
        if len(calls) == 1:
            raise OperationalError("lock", {}, Exception(code, "injected known abort"))
        return await original(db, account_id)

    monkeypatch.setattr(users, "lock_by_id", transient)
    result = await process_candidate(factory, identifier, env.settings, "worker.retry")
    assert result.outcome == Outcome.EXPIRED and result.reason is None and result.attempts == 2
    assert len(sessions) == 2 and all(not session.in_transaction() for session in sessions)
    assert await side_effect_counts(env) == (1, 2)


async def test_retry_exhaustion_is_bounded_and_leaves_no_side_effects(worker_env, monkeypatch):
    env = worker_env
    identifier, _ = await add_proposal(env)

    async def deadlock(db, account_id):
        raise OperationalError("lock", {}, Exception(1213, "injected deadlock"))

    with monkeypatch.context() as patch:
        patch.setattr(users, "lock_by_id", deadlock)
        settings = env.settings.model_copy(update={"max_deadlock_retries": 1})
        result = await process_candidate(
            env.factory, identifier, settings, "worker.retry-exhausted"
        )
    assert result.outcome == Outcome.FAILED and result.attempts == 2
    assert await side_effect_counts(env) == (0, 0)
    await assert_locks_released(env)


@pytest.mark.parametrize("limit", ["lock", "query", "request", "cancel"])
async def test_real_lock_waits_obey_budgets_and_cancel_cleanup(worker_env, monkeypatch, limit):
    env = worker_env
    identifier, _ = await add_proposal(env)
    updates: dict[str, float] = {
        "request_timeout_seconds": 5,
        "db_query_timeout_seconds": 4,
        "db_lock_timeout_seconds": 3,
    }
    if limit != "cancel":
        updates[
            {
                "lock": "db_lock_timeout_seconds",
                "query": "db_query_timeout_seconds",
                "request": "request_timeout_seconds",
            }[limit]
        ] = 0.1
    settings = env.settings.model_copy(update=updates)
    original = users.lock_by_id
    waiting = asyncio.Event()
    connection_ids = []
    async with env.factory.begin() as blocker:
        await original(blocker, env.staff_id)
        async with env.factory() as session:

            async def tracked(db, account_id):
                if account_id == env.staff_id:
                    connection_ids.append(await db.scalar(text("SELECT CONNECTION_ID()")))
                    waiting.set()
                return await original(db, account_id)

            with monkeypatch.context() as patch:
                patch.setattr(users, "lock_by_id", tracked)
                task = asyncio.create_task(
                    process_candidate(lambda: session, identifier, settings, "worker.lock-budget")
                )
                try:
                    if limit == "cancel":
                        async with asyncio.timeout(2):
                            await waiting.wait()
                            async with env.factory() as observer:
                                await wait_for_lock(observer, connection_ids[0])
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        async with asyncio.timeout(2):
                            result = await task
                        assert result.reason == FailureReason.DEADLINE_EXCEEDED
                    assert not session.in_transaction()
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    assert await side_effect_counts(env) == (0, 0)
    await assert_locks_released(env)


async def test_cleanup_failure_invalidates_without_publishing_exception_details(caplog):
    db = Mock()
    db.in_transaction.return_value = False
    db.close = AsyncMock(side_effect=RuntimeError("private-cleanup-payload"))
    db.invalidate = AsyncMock()
    await _finish_session(db, asyncio.get_running_loop().time() + 1)
    db.invalidate.assert_awaited_once()
    assert "private-cleanup-payload" not in caplog.text


async def test_cancellation_during_cleanup_never_starts_another_retry(worker_env, monkeypatch):
    env = worker_env
    identifier, _ = await add_proposal(env)
    session = env.factory()
    original_close = session.close
    cleaning = asyncio.Event()
    calls = []

    async def aborted(db, account_id):
        calls.append(account_id)
        raise OperationalError("lock", {}, Exception(1213, "known abort"))

    async def slow_close():
        await original_close()
        cleaning.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(users, "lock_by_id", aborted)
        patch.setattr(session, "close", slow_close)
        task = asyncio.create_task(
            process_candidate(lambda: session, identifier, env.settings, "worker.cleanup-cancel")
        )
        try:
            async with asyncio.timeout(2):
                await cleaning.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await session.close()
    assert len(calls) == 1 and await side_effect_counts(env) == (0, 0)
    await assert_locks_released(env)
