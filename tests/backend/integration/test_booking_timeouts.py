"""Configured booking budgets cancel MySQL work and leave recoverable transactions."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from app.api.errors import AppError
from app.domain.auth import AuthContext
from app.models import Appointment, AuditEvent, Notification, ServiceSetting
from app.repositories import users
from app.schemas.appointments import AppointmentCreateRequest
from app.services import booking


def _request(env, starts_at):
    return AppointmentCreateRequest(
        purpose_id=env.purpose_id, staff_id=env.staff_id, starts_at=starts_at
    )


async def _create(db, env, payload, settings, *, key="timeout-key", request_budget=3.0):
    return await booking.create_appointment(
        db,
        payload,
        context=AuthContext(
            session_id=uuid4(),
            user_id=env.student_id,
            email="student@example.invalid",
            name="Test Student",
            role="STUDENT",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        idempotency_key=key,
        request_id="booking-timeout-test",
        settings=settings,
        deadline=asyncio.get_running_loop().time() + request_budget,
    )


async def _assert_bundle_counts(factory, expected):
    async with factory() as db:
        counts = tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (Appointment, AuditEvent, Notification)
            ]
        )
    assert counts == expected


async def _assert_booking_locks_released(env):
    # Check before closing the timed-out session, so session teardown cannot hide a leak.
    async with asyncio.timeout(2):
        async with env.factory.begin() as fresh:
            assert await fresh.get(ServiceSetting, 1, with_for_update=True) is not None
            for account_id in sorted((env.student_id, env.staff_id), key=lambda value: value.bytes):
                assert await users.lock_by_id(fresh, account_id) is not None


@pytest.mark.parametrize(
    ("stage", "limiter"),
    [
        ("committed-key", "query"),
        ("duplicate-winner", "query"),
        ("eligibility", "query"),
        ("flush", "query"),
        ("committed-key", "request"),
        ("eligibility", "request"),
    ],
)
async def test_query_budget_cancels_database_work_and_cleans_transaction(
    booking_env, auth_settings, monkeypatch, stage, limiter
):
    env, _, starts_at = booking_env
    payload = _request(env, starts_at)
    replay = stage in {"committed-key", "duplicate-winner"}
    if replay:
        async with env.factory() as seed:
            receipt, _ = await _create(seed, env, payload, auth_settings)
            if stage == "duplicate-winner":
                async with seed.begin():
                    row = await seed.get(Appointment, receipt.id)
                    assert row is not None
                    row.status = "CANCELLED"
                    row.cancelled_at = await users.utc_now(seed)
    settings = auth_settings.model_copy(
        update={"db_query_timeout_seconds": 0.25 if limiter == "query" else 3.0}
    )
    entered = asyncio.Event()
    completed = asyncio.Event()

    async def slow_query(db):
        entered.set()
        await db.execute(text("SELECT SLEEP(0.8)"))
        completed.set()

    if replay:
        original_lookup = booking.appointments.find_creation_request
        reads = 0

        async def slow_lookup(db, **kwargs):
            nonlocal reads
            reads += 1
            if stage == "duplicate-winner" and reads <= 2:
                # Model a committed winner becoming visible only after the named unique error.
                return None
            await slow_query(db)
            return await original_lookup(db, **kwargs)

        monkeypatch.setattr(booking.appointments, "find_creation_request", slow_lookup)
    elif stage == "eligibility":
        original_purpose = booking.catalog.find_active_purpose

        async def slow_eligibility(db, purpose_id):
            await slow_query(db)
            return await original_purpose(db, purpose_id)

        monkeypatch.setattr(booking.catalog, "find_active_purpose", slow_eligibility)

    async with env.factory() as db:
        if stage == "flush":
            original_flush = db.flush

            async def slow_flush(*args, **kwargs):
                await original_flush(*args, **kwargs)
                await slow_query(db)

            monkeypatch.setattr(db, "flush", slow_flush)
        with pytest.raises(AppError) as caught:
            await _create(
                db,
                env,
                payload,
                settings,
                request_budget=0.25 if limiter == "request" else 3.0,
            )
        assert (caught.value.status_code, caught.value.code) == (503, "SERVICE_UNAVAILABLE")
        assert entered.is_set()
        assert not completed.is_set()
        assert not db.in_transaction()
        await _assert_booking_locks_released(env)
        await _assert_bundle_counts(env.factory, (1, 1, 2) if replay else (0, 0, 0))


@pytest.mark.parametrize("blocked_row", ["settings", "first-account", "second-account"])
async def test_configured_lock_budget_aborts_before_mysql_server_timeout(
    booking_env, auth_settings, blocked_row
):
    env, _, starts_at = booking_env
    payload = _request(env, starts_at)
    settings = auth_settings.model_copy(
        update={"db_query_timeout_seconds": 2.0, "db_lock_timeout_seconds": 0.1}
    )
    account_ids = sorted((env.student_id, env.staff_id), key=lambda value: value.bytes)
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock():
        async with env.factory.begin() as holder:
            if blocked_row == "settings":
                await holder.get(ServiceSetting, 1, with_for_update=True)
            else:
                index = 0 if blocked_row == "first-account" else 1
                await users.lock_by_id(holder, account_ids[index])
            acquired.set()
            await release.wait()

    async def release_later():
        await asyncio.sleep(0.8)
        release.set()

    holder_task = asyncio.create_task(hold_lock())
    release_task = None
    try:
        await asyncio.wait_for(acquired.wait(), timeout=5)
        release_task = asyncio.create_task(release_later())
        async with env.factory() as db:
            with pytest.raises(AppError) as caught:
                await _create(db, env, payload, settings)
            assert (caught.value.status_code, caught.value.code) == (503, "SERVICE_UNAVAILABLE")
            assert not db.in_transaction()
            await _assert_bundle_counts(env.factory, (0, 0, 0))
    finally:
        release.set()
        if release_task is not None:
            release_task.cancel()
            await asyncio.gather(release_task, return_exceptions=True)
        await holder_task

    await _assert_booking_locks_released(env)
    async with env.factory() as fresh:
        _, replayed = await _create(fresh, env, payload, auth_settings)
    assert replayed is False
    await _assert_bundle_counts(env.factory, (1, 1, 2))


@pytest.mark.parametrize("server_committed", [False, True], ids=["before-commit", "after-commit"])
async def test_query_timeout_during_commit_invalidates_without_retrying_writes(
    booking_env, auth_settings, monkeypatch, server_committed
):
    env, _, starts_at = booking_env
    payload = _request(env, starts_at)
    settings = auth_settings.model_copy(update={"db_query_timeout_seconds": 0.25})
    dialect = env.factory.kw["bind"].sync_engine.dialect
    original_commit = dialect.do_commit
    targets = set()
    entered = asyncio.Event()
    write_commits = 0

    def mark_write_connection(session):
        if any(isinstance(row, Appointment) for row in session.identity_map.values()):
            targets.add(id(session.connection().connection.dbapi_connection))

    def pause_commit(connection):
        nonlocal write_commits
        raw = getattr(connection, "dbapi_connection", connection)
        if id(raw) not in targets:
            return original_commit(connection)
        write_commits += 1
        entered.set()
        if server_committed:
            original_commit(connection)
        await_only(asyncio.sleep(0.8))
        if not server_committed:
            original_commit(connection)

    event.listen(Session, "before_commit", mark_write_connection)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(dialect, "do_commit", pause_commit)
            async with env.factory() as db:
                invalidations = 0
                original_invalidate = db.invalidate

                async def invalidate():
                    nonlocal invalidations
                    invalidations += 1
                    await original_invalidate()

                patch.setattr(db, "invalidate", invalidate)
                with pytest.raises(AppError) as caught:
                    await _create(db, env, payload, settings)
                assert (caught.value.status_code, caught.value.code) == (503, "SERVICE_UNAVAILABLE")
                assert entered.is_set()
                assert write_commits == 1
                assert invalidations == 1
                assert not db.in_transaction()
                await _assert_booking_locks_released(env)
    finally:
        event.remove(Session, "before_commit", mark_write_connection)

    await _assert_bundle_counts(env.factory, (1, 1, 2) if server_committed else (0, 0, 0))
    async with env.factory() as fresh:
        _, replayed = await _create(fresh, env, payload, auth_settings)
    assert replayed is server_committed
    await _assert_bundle_counts(env.factory, (1, 1, 2))
