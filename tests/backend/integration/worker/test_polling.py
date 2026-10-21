import asyncio
import json

import pytest
from sqlalchemy.exc import OperationalError
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.repositories import expiry_worker
from app.worker.batch import BatchRunner
from app.worker.polling import poll
from app.worker.reporting import Reporter

pytestmark = pytest.mark.mysql


async def test_outage_is_failed_scan_not_empty_scan_and_recovery_processes_persisted_backlog(
    worker_env, monkeypatch
):
    env = worker_env
    await add_proposal(env)
    original = expiry_worker.discover
    calls = []

    async def unavailable_then_recover(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OperationalError(
                "private SQL", {}, Exception(2003, "private database credentials")
            )
        return await original(*args, **kwargs)

    monkeypatch.setattr(expiry_worker, "discover", unavailable_then_recover)
    output, delays = [], []
    runner = BatchRunner(env.factory, env.settings, reporter=Reporter(output.append))
    stop = asyncio.Event()

    async def wait(event, seconds):
        delays.append(seconds)
        if len(delays) == 2:
            stop.set()
        return stop.is_set()

    assert await poll(runner, stop, wait=wait) == 0
    assert len(calls) == 2 and runner.state.failed_scans == runner.state.successful_scans == 1
    assert runner.state.database_ok and runner.state.expired == 1
    assert await side_effect_counts(env) == (1, 2)
    scans = [json.loads(line) for line in output if json.loads(line)["event"] == "scan"]
    assert scans[0]["last_successful_scan"] is None and scans[0]["database_ok"] is False
    assert scans[1]["last_successful_scan"] and scans[1]["database_ok"]
    assert "private" not in "".join(output)
    assert delays == [5, 5]  # first bounded outage delay, then normal polling after recovery


@pytest.mark.parametrize("inconsistent", [False, True])
async def test_single_batch_exit_code_and_successful_empty_scans(worker_env, inconsistent):
    env = worker_env
    if inconsistent:
        await add_proposal(env, appointment_status="UPCOMING")
    runner = BatchRunner(env.factory, env.settings)
    assert await poll(runner, asyncio.Event(), once=True) == (1 if inconsistent else 0)
    assert runner.state.successful_scans == 1 and runner.state.failed_scans == 0
    assert runner.state.database_ok
