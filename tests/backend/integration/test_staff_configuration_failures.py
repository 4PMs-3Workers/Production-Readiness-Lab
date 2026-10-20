"""Atomic configuration audits, bounded failures and uncertain-commit recovery."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_cancellation_concurrency import wait_for_lock
from tests.backend.integration.test_staff_configuration import (
    assert_configuration_locks_released,
    counts,
    mutate,
)

from app.core.errors import AppError
from app.models import AuditEvent, StaffPurpose, StaffUnavailability
from app.repositories import users
from app.services import staff_availability as service

pytestmark = pytest.mark.mysql


async def prepare_delete(env, start, kind):
    if kind != "delete":
        return None
    async with env.factory.begin() as db:
        period = StaffUnavailability(
            staff_id=env.staff_id, starts_at=start, ends_at=start + timedelta(minutes=30)
        )
        db.add(period)
        await db.flush()
        return period.id


async def assert_original(env, kind, identifier):
    values = await counts(env)
    assert values["audit_events"] == values["notifications"] == 0
    assert values["staff_unavailability"] == (1 if kind == "delete" else 0)
    async with env.factory() as db:
        assert (await db.get(StaffPurpose, (env.staff_id, env.purpose_id))).is_accepting
        if identifier is not None:
            assert await db.get(StaffUnavailability, identifier) is not None


@pytest.mark.parametrize("kind", ["purpose", "create", "delete"])
async def test_audit_failure_rolls_back_each_configuration_change(booking_env, kind):
    env, _, start = booking_env
    identifier = await prepare_delete(env, start, kind)

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events"):
            raise OperationalError("audit", {}, Exception(1644, "injected audit failure"))

    engine = env.app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", fail_audit)
    try:
        async with env.factory() as db:
            with pytest.raises(AppError) as error:
                await mutate(env, kind, start, identifier=identifier, db=db)
            assert error.value.code == "SERVICE_UNAVAILABLE"
            assert not db.in_transaction()
            await assert_configuration_locks_released(env)
    finally:
        event.remove(engine, "before_cursor_execute", fail_audit)
    await assert_original(env, kind, identifier)


@pytest.mark.parametrize("kind", ["purpose", "create", "delete"])
@pytest.mark.parametrize("committed", [True, False])
async def test_unknown_commit_is_not_auto_replayed_and_explicit_recovery_has_no_duplicates(
    booking_env, monkeypatch, kind, committed
):
    env, _, start = booking_env
    identifier = await prepare_delete(env, start, kind)
    original_commit = AsyncSessionTransaction.commit
    attempts = []
    async with env.factory() as db:

        async def uncertain(transaction):
            if transaction.session is not db:
                return await original_commit(transaction)
            attempts.append(transaction)
            if committed:
                await original_commit(transaction)
            raise OperationalError("COMMIT", {}, Exception(2013, "connection lost during commit"))

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSessionTransaction, "commit", uncertain)
            with pytest.raises(AppError) as error:
                await mutate(env, kind, start, identifier=identifier, db=db)
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert len(attempts) == 1
        assert not db.in_transaction()
        await assert_configuration_locks_released(env)
    current = await counts(env)
    assert current["audit_events"] == (1 if committed else 0)
    if kind == "delete" and committed:
        # GET owned list is the recovery mechanism; a repeated DELETE is still 404.
        async with env.factory() as db:
            assert await db.get(StaffUnavailability, identifier) is None
        with pytest.raises(AppError) as missing:
            await mutate(env, kind, start, identifier=identifier)
        assert missing.value.code == "NOT_FOUND"
    else:
        result = await mutate(env, kind, start, identifier=identifier)
        if kind == "create":
            assert result[1] is committed
            assert (await counts(env))["staff_unavailability"] == 1
    assert (await counts(env))["audit_events"] == 1
    assert (await counts(env))["notifications"] == 0


@pytest.mark.parametrize("code", [1205, 1213])
async def test_known_aborted_lock_failures_retry_the_complete_transaction(
    booking_env, monkeypatch, code
):
    env, _, start = booking_env
    original_lock = users.lock_by_id
    calls = []

    async def transient(db, identifier):
        calls.append(identifier)
        if len(calls) == 1:
            raise OperationalError("lock", {}, Exception(code, "known-aborted lock failure"))
        return await original_lock(db, identifier)

    monkeypatch.setattr(users, "lock_by_id", transient)
    result, replay = await mutate(env, "create", start)
    assert result.id and not replay and len(calls) == 2
    assert (await counts(env))["audit_events"] == 1


async def test_deadlock_retry_budget_is_bounded(booking_env, monkeypatch):
    env, _, start = booking_env
    calls = []

    async def always_deadlocks(db, identifier):
        calls.append(identifier)
        raise OperationalError("lock", {}, Exception(1213, "deadlock"))

    monkeypatch.setattr(users, "lock_by_id", always_deadlocks)
    settings = env.app.state.settings.model_copy(update={"max_deadlock_retries": 1})
    with pytest.raises(AppError) as error:
        await mutate(env, "create", start, settings=settings)
    assert error.value.code == "SERVICE_UNAVAILABLE" and len(calls) == 2
    await assert_original(env, "create", None)


@pytest.mark.parametrize("limit", ["lock", "query", "request", "cancel"])
async def test_real_lock_waits_obey_deadlines_and_release_resources(
    booking_env, monkeypatch, limit
):
    env, _, start = booking_env
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
    settings = env.app.state.settings.model_copy(update=updates)
    waiting = asyncio.Event()
    connection_ids = []
    original_lock = users.lock_by_id

    async def tracked_lock(db, identifier):
        connection_ids.append(await db.scalar(text("SELECT CONNECTION_ID()")))
        waiting.set()
        return await original_lock(db, identifier)

    async with env.factory.begin() as blocker:
        await users.lock_by_id(blocker, env.staff_id)
        monkeypatch.setattr(users, "lock_by_id", tracked_lock)
        async with env.factory() as db:
            task = asyncio.create_task(mutate(env, "create", start, db=db, settings=settings))
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
                    with pytest.raises(AppError) as error:
                        async with asyncio.timeout(2):
                            await task
                    assert error.value.code == "SERVICE_UNAVAILABLE"
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert not db.in_transaction()
    await assert_configuration_locks_released(env)
    await assert_original(env, "create", None)


async def test_timeout_after_writes_rolls_back_period_and_audit(booking_env, monkeypatch):
    env, _, start = booking_env
    entered = asyncio.Event()
    settings = env.app.state.settings.model_copy(update={"db_query_timeout_seconds": 0.1})
    async with env.factory() as db:
        original_commit = service.commit_safely

        async def delayed_commit(session, transaction):
            assert await session.scalar(select(AuditEvent.id)) is not None
            assert await session.scalar(select(StaffUnavailability.id)) is not None
            entered.set()
            await asyncio.sleep(5)
            await original_commit(session, transaction)

        monkeypatch.setattr(service, "commit_safely", delayed_commit)
        with pytest.raises(AppError) as error:
            await mutate(env, "create", start, db=db, settings=settings)
        assert error.value.code == "SERVICE_UNAVAILABLE" and entered.is_set()
        assert not db.in_transaction()
        await assert_configuration_locks_released(env)
    await assert_original(env, "create", None)


async def test_mutation_acquires_policy_then_staff_then_configuration_locks(booking_env):
    env, _, start = booking_env
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or "LOCK IN SHARE MODE" in statement:
            statements.append(statement)

    engine = env.app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        await mutate(env, "purpose", start)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert "service_settings" in statements[0] and "LOCK IN SHARE MODE" in statements[0]
    assert "users" in statements[1] and "FOR UPDATE" in statements[1]
    assert "staff_purposes" in statements[-1] and "FOR UPDATE" in statements[-1]
