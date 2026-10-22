import asyncio
import json

import pytest
from sqlalchemy import select, text
from tests.backend.integration.test_cancellation_concurrency import ordered_race
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts
from tests.backend.integration.worker.test_entrypoint import invoke

from app.models import AuditEvent, ChangeProposal
from app.repositories import expiry_worker, users
from app.worker.batch import BatchRunner

pytestmark = pytest.mark.mysql


async def test_workers_discover_the_same_id_then_serialize_expiry_and_noop(
    worker_env, monkeypatch, worker_reports
):
    env = worker_env
    identifier, _ = await add_proposal(env)
    settings = env.settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )
    runners = []

    async def run(db):
        runner = BatchRunner(lambda: db, settings, reporter=worker_reports())
        runners.append(runner)
        return await runner.run_batch()

    results = await ordered_race(
        env, monkeypatch, min(env.student_id, env.staff_id, key=lambda value: value.bytes), run, run
    )
    assert results == [True, True]
    assert (runners[0].state.expired, runners[1].state.noops) == (1, 1)
    assert sum(runner.state.failed for runner in runners) == 0
    assert await side_effect_counts(env) == (1, 2)
    first_run_id = runners[0].state.current_run_id
    assert first_run_id is not None
    async with env.factory() as db:
        audit = (await db.scalars(select(AuditEvent))).one()
        assert audit.request_id == runners[0].identity.request_id(first_run_id, identifier)


async def test_two_full_batches_race_without_duplicate_effects(
    worker_env, monkeypatch, worker_reports
):
    env = worker_env
    for _ in range(2):
        await add_proposal(env)
    original = expiry_worker.discover
    both_discovered = asyncio.Event()
    snapshots = []

    async def discover(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        snapshots.append(snapshot)
        if len(snapshots) == 2:
            both_discovered.set()
        async with asyncio.timeout(3):
            await both_discovered.wait()
        return snapshot

    monkeypatch.setattr(expiry_worker, "discover", discover)
    settings = env.settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )
    runners = [BatchRunner(env.factory, settings, reporter=worker_reports()) for _ in range(2)]
    await asyncio.gather(*(runner.run_batch() for runner in runners))
    assert all(len(snapshot.candidates) == 2 for snapshot in snapshots)
    assert sum(runner.state.expired for runner in runners) == 2
    assert sum(runner.state.noops for runner in runners) == 2
    assert sum(runner.state.failed for runner in runners) == 0
    assert await side_effect_counts(env) == (2, 4)
    async with env.factory() as db:
        assert {row.status for row in (await db.scalars(select(ChangeProposal))).all()} == {
            "EXPIRED"
        }


async def test_two_independent_worker_processes_wait_on_real_locks_and_commit_once(worker_env):
    env = worker_env
    await add_proposal(env)
    overrides = {
        "REQUEST_TIMEOUT_SECONDS": "15",
        "DB_QUERY_TIMEOUT_SECONDS": "10",
        "DB_LOCK_TIMEOUT_SECONDS": "8",
    }
    tasks = []
    try:
        async with env.factory.begin() as blocker:
            await users.lock_by_id(blocker, env.staff_id)
            tasks = [
                asyncio.create_task(invoke(env, "--once", env_overrides=overrides))
                for _ in range(2)
            ]
            async with env.factory() as observer:
                async with asyncio.timeout(7):
                    while (
                        await observer.scalar(
                            text(
                                "SELECT COUNT(DISTINCT w.REQUESTING_THREAD_ID) "
                                "FROM performance_schema.data_lock_waits w "
                                "JOIN performance_schema.data_locks l "
                                "ON l.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID "
                                "AND l.ENGINE=w.ENGINE "
                                "WHERE l.OBJECT_SCHEMA=:schema AND l.OBJECT_NAME='users'"
                            ),
                            {"schema": env.engine.url.database},
                        )
                        != 2
                    ):
                        await asyncio.sleep(0)
        results = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    states = []
    for code, output, errors in results:
        assert code == 0, errors
        states.append(json.loads(output.splitlines()[-1]))
    assert len({state["worker_id"] for state in states}) == 2
    assert sum(state["expired"] for state in states) == sum(state["noops"] for state in states) == 1
    assert await side_effect_counts(env) == (1, 2)
