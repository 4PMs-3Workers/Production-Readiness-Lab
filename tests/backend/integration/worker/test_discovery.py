from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import event
from tests.backend.integration.worker.helpers import add_proposal

from app.repositories import expiry_worker, users

pytestmark = pytest.mark.mysql


async def test_discovery_is_due_only_ordered_bounded_nonlocking_and_reports_backlog(worker_env):
    env = worker_env
    due = env.now - timedelta(minutes=1)
    for identifier in [UUID(int=3), UUID(int=2), UUID(int=1)]:
        await add_proposal(env, identifier=identifier, expiry=due)
    await add_proposal(env, expiry=env.now + timedelta(days=1))
    await add_proposal(env, proposal_status="EXPIRED", appointment_status="CANCELLED")
    settings = env.settings.model_copy(update={"worker_batch_size": 2})
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(env.engine.sync_engine, "before_cursor_execute", record)
    try:
        async with env.factory() as db:
            first = await expiry_worker.discover(db, settings)
            assert not db.in_transaction()
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", record)
    assert [row.proposal_id for row in first.candidates] == [UUID(int=1), UUID(int=2)]
    assert first.backlog_count == 2 and first.backlog_capped
    assert first.oldest_overdue_seconds is not None
    assert first.oldest_overdue_seconds >= 60
    assert all(statement.startswith("SELECT") for statement in statements)
    assert all(
        "FOR UPDATE" not in statement and "LOCK IN SHARE MODE" not in statement
        for statement in statements
    )
    async with env.factory() as db:
        second = await expiry_worker.discover(db, settings, after=first.candidates[-1])
    assert [row.proposal_id for row in second.candidates] == [UUID(int=3)]


async def test_discovery_uses_database_clock_and_includes_exact_expiry(worker_env, monkeypatch):
    env = worker_env
    identifier, _ = await add_proposal(env, expiry=env.now)
    calls = []

    async def clock(db):
        calls.append(db)
        return env.now

    monkeypatch.setattr(users, "utc_now", clock)
    async with env.factory() as db:
        snapshot = await expiry_worker.discover(db, env.settings)
    assert calls and snapshot.database_now == env.now
    assert snapshot.candidates[0].proposal_id == identifier
    assert snapshot.oldest_overdue_seconds == 0


async def test_successful_empty_discovery_is_not_a_database_failure(worker_env):
    env = worker_env
    async with env.factory() as db:
        snapshot = await expiry_worker.discover(db, env.settings)
    assert snapshot.candidates == () and snapshot.backlog_count == 0
    assert snapshot.backlog_capped is False and snapshot.oldest_overdue_seconds is None
