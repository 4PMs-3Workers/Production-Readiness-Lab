import asyncio
import json
from datetime import timedelta

import pytest
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.models import Appointment, ChangeProposal
from app.repositories import users
from app.worker.batch import BatchRunner
from app.worker.processing import process_candidate
from app.worker.reporting import Reporter

pytestmark = pytest.mark.mysql


async def test_failed_candidate_does_not_prevent_the_rest_of_the_selected_batch(worker_env):
    env = worker_env
    await add_proposal(env, appointment_status="COMPLETED")
    await add_proposal(env)
    await add_proposal(env)
    output = []
    runner = BatchRunner(env.factory, env.settings, reporter=Reporter(output.append))
    assert await runner.run_batch()
    assert (runner.state.expired, runner.state.failed, runner.state.selected) == (2, 1, 3)
    assert await side_effect_counts(env) == (2, 4)
    assert json.loads(output[-1])["batch_failed"] == 1
    assert "private-worker-fixture-consultation" not in "".join(output)


async def test_small_batches_pass_poison_rows_and_wrap_to_recover_them(worker_env):
    env = worker_env
    bad_id, appointment_id = await add_proposal(
        env, expiry=env.now - timedelta(minutes=2), appointment_status="UPCOMING"
    )
    await add_proposal(env, expiry=env.now - timedelta(minutes=1))
    await add_proposal(env)
    runner = BatchRunner(env.factory, env.settings.model_copy(update={"worker_batch_size": 1}))
    for _ in range(4):
        await runner.run_batch()
    assert runner.state.failed == 1 and runner.state.expired == 2
    assert runner.cursor is None  # successful empty end-of-sweep scan resets the cursor
    async with env.factory.begin() as db:
        row = await db.get(Appointment, appointment_id)
        assert row
        row.status = "CHANGE_PENDING"
    await runner.run_batch()
    assert runner.cursor and runner.cursor.proposal_id == bad_id
    assert await side_effect_counts(env) == (3, 6)


async def test_finite_sweep_excludes_newly_due_rows_until_old_failed_rows_can_be_revisited(
    worker_env, monkeypatch
):
    env = worker_env
    await add_proposal(env, expiry=env.now - timedelta(seconds=2))
    await add_proposal(env, expiry=env.now - timedelta(seconds=1))
    now = env.now

    async def clock(db):
        return now

    monkeypatch.setattr(users, "utc_now", clock)
    runner = BatchRunner(env.factory, env.settings.model_copy(update={"worker_batch_size": 1}))
    await runner.run_batch()
    new_id, _ = await add_proposal(env, expiry=env.now + timedelta(seconds=1))
    now += timedelta(seconds=2)
    await runner.run_batch()
    await runner.run_batch()
    assert runner.cursor is None and runner.state.expired == 2
    await runner.run_batch()
    assert runner.cursor and runner.cursor.proposal_id == new_id
    assert await side_effect_counts(env) == (3, 6)


async def test_stop_does_not_accept_next_candidate_and_restart_reads_remaining_rows(worker_env):
    env = worker_env
    for _ in range(3):
        await add_proposal(env)
    stop = asyncio.Event()

    async def process_then_stop(*args):
        result = await process_candidate(*args)
        stop.set()
        return result

    runner = BatchRunner(env.factory, env.settings, processor=process_then_stop)
    await runner.run_batch(stop)
    assert runner.state.expired == 1
    assert await runner.run_batch(stop) is False
    restarted = BatchRunner(env.factory, env.settings)
    await restarted.run_batch()
    assert restarted.state.expired == 2
    assert await side_effect_counts(env) == (3, 6)


async def test_reporting_failure_cannot_undo_committed_expiry(worker_env):
    env = worker_env
    identifier, _ = await add_proposal(env)

    def broken_sink(value):
        raise OSError("private-logging-failure")

    runner = BatchRunner(env.factory, env.settings, reporter=Reporter(broken_sink))
    await runner.run_batch()
    assert runner.state.expired == 1 and runner.state.reporting_failures == 2
    async with env.factory() as db:
        row = await db.get(ChangeProposal, identifier)
        assert row and row.status == "EXPIRED"
    assert await side_effect_counts(env) == (1, 2)
