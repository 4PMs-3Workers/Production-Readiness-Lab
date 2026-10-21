"""Stop-aware polling and independent heartbeat; database state remains authoritative."""

import asyncio
from collections.abc import Awaitable, Callable

from app.worker.batch import BatchRunner, ScanFailure
from app.worker.models import FailureReason
from app.worker.resources import SchemaCompatibilityError

Waiter = Callable[[asyncio.Event, float], Awaitable[bool]]


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    if stop.is_set():
        return True
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
        return True
    except TimeoutError:
        return False


def failure_backoff(poll_seconds: float, failures: int) -> float:
    base = max(0.1, min(poll_seconds, 60.0))
    return min(base * 2 ** min(max(failures - 1, 0), 10), 60.0)


async def poll(
    runner: BatchRunner,
    stop: asyncio.Event,
    *,
    once: bool = False,
    wait: Waiter = wait_or_stop,
) -> int:
    failures = 0
    while not stop.is_set():
        try:
            if not await runner.run_batch(stop):
                break
        except SchemaCompatibilityError:
            runner.reporter.emit("fatal", runner.state, reason=FailureReason.SCHEMA_INCOMPATIBLE)
            return 2
        except ScanFailure:
            failures += 1
            if once:
                return 1
            delay = failure_backoff(runner.settings.worker_poll_interval_seconds, failures)
        else:
            if once:
                return 1 if runner.state.batch_failed else 0
            if runner.state.database_ok:
                failures = 0
                delay = runner.settings.worker_poll_interval_seconds
            else:
                failures += 1
                delay = failure_backoff(runner.settings.worker_poll_interval_seconds, failures)
        if await wait(stop, delay):
            break
    return 0


async def heartbeat(runner: BatchRunner, stop: asyncio.Event) -> None:
    while not stop.is_set():
        runner.reporter.emit("heartbeat", runner.state)
        await wait_or_stop(stop, runner.settings.worker_heartbeat_interval_seconds)
