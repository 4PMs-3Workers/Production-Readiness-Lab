import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.worker.batch import BatchRunner
from app.worker.polling import failure_backoff, heartbeat, poll, wait_or_stop
from app.worker.reporting import Reporter


@pytest.mark.parametrize("size", [0, -1, 1001, 2**64, 1.5])
def test_worker_batch_configuration_rejects_unbounded_or_invalid_limits(auth_settings, size):
    values = auth_settings.model_dump(by_alias=True)
    values["WORKER_BATCH_SIZE"] = size
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.parametrize("size", [1, 100, 1000])
def test_worker_batch_configuration_accepts_its_documented_bounds(auth_settings, size):
    values = auth_settings.model_dump(by_alias=True)
    values["WORKER_BATCH_SIZE"] = size
    assert Settings.model_validate(values).worker_batch_size == size


def test_outage_backoff_is_exponential_capped_and_never_a_hot_loop():
    assert [failure_backoff(5, failures) for failures in range(1, 7)] == [5, 10, 20, 40, 60, 60]
    assert failure_backoff(0.001, 1) == 0.1
    assert failure_backoff(1e300, 10**10) == 60


async def test_wait_is_immediately_interruptible_even_for_long_poll_intervals():
    stop = asyncio.Event()
    waiting = asyncio.create_task(wait_or_stop(stop, 1e6))
    stop.set()
    async with asyncio.timeout(1):
        assert await waiting
    assert await wait_or_stop(asyncio.Event(), 0.001) is False


async def test_heartbeat_keeps_running_while_database_scan_is_slow(auth_settings):
    stop = asyncio.Event()
    scan_started, release_scan, enough_heartbeats = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    output = []

    def sink(value):
        output.append(json.loads(value))
        if len([row for row in output if row["event"] == "heartbeat"]) >= 3:
            loop.call_soon_threadsafe(enough_heartbeats.set)

    loop = asyncio.get_running_loop()
    settings = auth_settings.model_copy(update={"worker_heartbeat_interval_seconds": 0.01})
    runner = BatchRunner(AsyncMock(), settings, reporter=Reporter(sink))

    async def slow_scan(stop: asyncio.Event | None = None) -> bool:
        scan_started.set()
        await release_scan.wait()
        return True

    runner.run_batch = slow_scan
    work = asyncio.create_task(poll(runner, stop))
    pulse = asyncio.create_task(heartbeat(runner, stop))
    try:
        async with asyncio.timeout(1):
            await scan_started.wait()
            await enough_heartbeats.wait()
        assert all(row["last_successful_scan"] is None and not row["database_ok"] for row in output)
    finally:
        stop.set()
        release_scan.set()
        await asyncio.gather(work, pulse)
        await runner.reporter.close()
