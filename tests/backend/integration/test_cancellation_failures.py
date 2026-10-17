"""Real MySQL rollback, lock deadline, and ambiguous cancellation recovery."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_booking_service import ZONE
from tests.backend.integration.test_cancellation_concurrency import (
    assert_cancel_bundle,
    ordered_race,
    wait_for_lock,
)
from tests.backend.integration.test_student_cancellation import cancel, seed

from app.api.errors import AppError
from app.models import Appointment, AuditEvent, ChangeProposal, Notification, ServiceSetting
from app.repositories import users


async def pending(env, start):
    appointment_id = await seed(env, start, "CHANGE_PENDING")
    async with env.factory.begin() as db:
        db.add(
            ChangeProposal(
                appointment_id=appointment_id,
                created_by=env.staff_id,
                proposed_starts_at=start + timedelta(hours=1),
                proposed_ends_at=start + timedelta(hours=2),
                expires_at=start,
            )
        )
    return appointment_id


async def assert_untouched(env, appointment_id):
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert row.status == "CHANGE_PENDING"
        assert row.cancelled_at is None and row.cancelled_by is None and row.cancel_reason is None
        proposal = await db.scalar(select(ChangeProposal))
        assert proposal.status == "PENDING"
        assert proposal.resolved_at is None and proposal.resolved_by is None
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def assert_locks_released(env, appointment_id):
    # Called while the failed session is still open: teardown cannot hide a leak.
    async with asyncio.timeout(2):
        async with env.factory.begin() as fresh:
            await fresh.get(ServiceSetting, 1, with_for_update=True)
            for account_id in sorted((env.student_id, env.staff_id), key=lambda value: value.bytes):
                await users.lock_by_id(fresh, account_id)
            await fresh.get(Appointment, appointment_id, with_for_update=True)
            await fresh.scalar(select(ChangeProposal).with_for_update())


@pytest.mark.parametrize("stage", ["appointment", "proposal", "audit", "second-notification"])
async def test_each_write_failure_rolls_back_full_pending_bundle(booking_env, auth_settings, stage):
    env, _, start = booking_env
    appointment_id = await pending(env, start)
    table, action = {
        "appointment": ("appointments", "UPDATE"),
        "proposal": ("change_proposals", "UPDATE"),
        "audit": ("audit_events", "INSERT"),
        "second-notification": ("notifications", "INSERT"),
    }[stage]
    condition = ""
    if stage == "second-notification":
        condition = (
            "IF (SELECT COUNT(*) FROM notifications WHERE "
            "appointment_id=NEW.appointment_id) >= 1 THEN "
        )
    trigger = (
        f"CREATE TRIGGER cancellation_failure BEFORE {action} ON {table} FOR EACH ROW BEGIN "
        f"{condition}SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='injected private failure'; "
        f"{'END IF; ' if condition else ''}END"
    )
    engine = env.factory.kw["bind"]
    async with engine.begin() as connection:
        await connection.exec_driver_sql(trigger)
    try:
        async with env.factory() as db:
            with pytest.raises(AppError) as error:
                await cancel(env, appointment_id, auth_settings, db=db, reason="private")
            assert (error.value.status_code, error.value.code) == (503, "SERVICE_UNAVAILABLE")
            assert "private" not in error.value.message
            assert not db.in_transaction()
            await assert_locks_released(env, appointment_id)
        await assert_untouched(env, appointment_id)
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("DROP TRIGGER cancellation_failure")


async def test_cutoff_is_rechecked_after_real_lock_wait(booking_env, auth_settings, monkeypatch):
    env, date, start = booking_env
    appointment_id = await pending(env, start)
    midnight = datetime.combine(date, datetime.min.time(), ZONE).astimezone(UTC)
    now = midnight - timedelta(microseconds=1)
    clock_calls = []

    async def clock(db):
        clock_calls.append(now)
        return now

    monkeypatch.setattr(users, "utc_now", clock)

    async def blocker(db):
        async with db.begin():
            await users.lock_by_id(db, env.student_id)

    def advance():
        nonlocal now
        assert clock_calls == []
        now = midnight

    _, result = await ordered_race(
        env,
        monkeypatch,
        env.student_id,
        blocker,
        lambda db: cancel(env, appointment_id, auth_settings, db=db),
        before_release=advance,
    )
    assert isinstance(result, AppError)
    assert result.code == "CANCELLATION_DEADLINE_PASSED"
    assert clock_calls == [midnight]
    await assert_untouched(env, appointment_id)


async def test_query_timeout_after_flush_rolls_back_all_written_rows(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    appointment_id = await pending(env, start)
    settings = auth_settings.model_copy(update={"db_query_timeout_seconds": 0.25})
    entered = asyncio.Event()
    completed = asyncio.Event()
    async with env.factory() as db:
        original_flush = db.flush

        async def flush_then_query():
            await original_flush()
            # These reads prove all four kinds of writes reached MySQL before cancellation.
            assert (await db.get(Appointment, appointment_id)).status == "CANCELLED"
            assert (await db.scalar(select(ChangeProposal))).status == "CANCELLED"
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
            assert await db.scalar(select(func.count()).select_from(Notification)) == 2
            entered.set()
            # Leave headroom for the separate two-second lock-release assertion:
            # the server may finish this query before noticing the client disconnect.
            await db.execute(text("SELECT SLEEP(1)"))
            completed.set()

        monkeypatch.setattr(db, "flush", flush_then_query)
        with pytest.raises(AppError) as error:
            await cancel(env, appointment_id, settings, db=db, reason="private")
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert entered.is_set() and not completed.is_set()
        assert not db.in_transaction()
        await assert_locks_released(env, appointment_id)
        await assert_untouched(env, appointment_id)


@pytest.mark.parametrize("limiter", ["lock", "query", "cancel-task"])
async def test_real_lock_wait_abort_cleans_session_and_releases_all_locks(
    booking_env, auth_settings, monkeypatch, limiter
):
    env, _, start = booking_env
    appointment_id = await pending(env, start)
    settings = auth_settings.model_copy(
        update={
            "db_lock_timeout_seconds": 0.5 if limiter == "lock" else 4,
            "db_query_timeout_seconds": 0.5 if limiter == "query" else 5,
        }
    )
    entering = asyncio.Event()
    original = users.lock_by_id
    ids = []
    async with env.factory() as holder, env.factory() as db, env.factory() as observer:
        async with holder.begin():
            await original(holder, env.student_id)
            holder_id = await holder.scalar(text("SELECT CONNECTION_ID()"))
            observer_id = await observer.scalar(text("SELECT CONNECTION_ID()"))

            async def lock(session, account_id):
                if session is db and account_id == env.student_id:
                    ids.append(await session.scalar(text("SELECT CONNECTION_ID()")))
                    entering.set()
                return await original(session, account_id)

            monkeypatch.setattr(users, "lock_by_id", lock)
            task = asyncio.create_task(cancel(env, appointment_id, settings, db=db))
            try:
                async with asyncio.timeout(2):
                    await entering.wait()
                await wait_for_lock(observer, ids[0])
                assert len({holder_id, observer_id, ids[0]}) == 3
                if limiter == "cancel-task":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(AppError) as error:
                        await task
                    assert error.value.code == "SERVICE_UNAVAILABLE"
                assert not db.in_transaction()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await assert_locks_released(env, appointment_id)
        await assert_untouched(env, appointment_id)
        assert (await cancel(env, appointment_id, auth_settings)).status == "CANCELLED"


@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_commit_is_not_retried_and_fresh_read_retry_recovers(
    booking_env, auth_settings, monkeypatch, committed
):
    env, _, start = booking_env
    appointment_id = await pending(env, start)
    original = AsyncSessionTransaction.commit
    commits, invalidations = [], []
    async with env.factory() as db:
        original_invalidate = db.invalidate

        async def ambiguous(transaction):
            commits.append(True)
            if committed:
                await original(transaction)
            raise OperationalError("COMMIT", {}, Exception(2013, "lost acknowledgement"))

        async def invalidate():
            invalidations.append(True)
            await original_invalidate()

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSessionTransaction, "commit", ambiguous)
            patch.setattr(db, "invalidate", invalidate)
            with pytest.raises(AppError) as error:
                await cancel(env, appointment_id, auth_settings, db=db, reason="first")
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert "rollback" not in error.value.message.lower()
        assert commits == [True] and invalidations == [True]
        assert not db.in_transaction()
        await assert_locks_released(env, appointment_id)
    original_time = None
    if committed:
        original_time = await assert_cancel_bundle(env, appointment_id, "first")
    else:
        await assert_untouched(env, appointment_id)
    result = await cancel(env, appointment_id, auth_settings, reason="retry")
    if committed:
        assert result.cancelled_at == original_time
    await assert_cancel_bundle(env, appointment_id, "first" if committed else "retry")
    async with env.factory() as db:
        proposal = await db.scalar(select(ChangeProposal))
        assert proposal.status == "CANCELLED"
        assert proposal.resolved_at == result.cancelled_at
