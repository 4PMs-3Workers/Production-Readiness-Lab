"""Process lifecycle with one grace deadline shared by work and resource cleanup."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.database import create_database_engine
from app.worker.batch import BatchRunner
from app.worker.models import FailureReason, WorkerIdentity, WorkerState
from app.worker.polling import heartbeat, poll
from app.worker.processing import SessionFactory, process_candidate
from app.worker.reporting import Reporter
from app.worker.resources import ResourceCleanupError, open_resources


class Shutdown:
    def __init__(self, grace_seconds: float) -> None:
        self.stop = asyncio.Event()
        self.grace_seconds = grace_seconds
        self.deadline: float | None = None

    def request_stop(self) -> None:
        if not self.stop.is_set():
            self.deadline = asyncio.get_running_loop().time() + self.grace_seconds
            self.stop.set()


def _consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _drain(work: asyncio.Task[int], shutdown: Shutdown) -> int:
    assert shutdown.deadline is not None
    remaining = max(0.0, shutdown.deadline - asyncio.get_running_loop().time())
    done, _ = await asyncio.wait({work}, timeout=remaining * 0.7)
    if not done:
        work.cancel()
        done, _ = await asyncio.wait(
            {work}, timeout=max(0.0, shutdown.deadline - asyncio.get_running_loop().time())
        )
    if not done:
        work.cancel()
        work.add_done_callback(_consume)
        return 1
    return 0 if work.cancelled() else work.result()


async def run_worker(
    settings: Settings,
    *,
    shutdown: Shutdown | None = None,
    once: bool = False,
    engine_factory: Callable[..., AsyncEngine] = create_database_engine,
    reporter: Reporter | None = None,
) -> int:
    control = shutdown or Shutdown(settings.worker_grace_seconds)
    identity = WorkerIdentity()
    state = WorkerState(identity.worker_id)
    reports = reporter or Reporter()
    reports.emit("starting", state)
    result = 1
    if control.stop.is_set():
        state.phase = "stopped"
        reports.emit("stopped", state)
        await reports.close(control.deadline)
        return 0
    try:
        async with open_resources(
            settings, engine_factory=engine_factory, cleanup_deadline=lambda: control.deadline
        ) as resources:

            async def process(
                sessions: SessionFactory, identifier: UUID, config: Settings, request_id: str
            ):
                return await process_candidate(
                    sessions,
                    identifier,
                    config,
                    request_id,
                    cleanup_deadline=lambda: control.deadline,
                )

            runner = BatchRunner(
                resources.sessions,
                settings,
                reporter=reports,
                identity=identity,
                processor=process,
                cleanup_deadline=lambda: control.deadline,
            )
            runner.state = state
            heartbeat_stop = asyncio.Event()
            pulse = asyncio.create_task(heartbeat(runner, heartbeat_stop), name="expiry-heartbeat")
            work = asyncio.create_task(poll(runner, control.stop, once=once), name="expiry-poll")
            stop_waiter = asyncio.create_task(control.stop.wait(), name="expiry-stop")
            try:
                await asyncio.wait({work, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
                if work.done():
                    result = work.result()
            finally:
                control.request_stop()
                state.phase = "stopping"
                reports.emit("stopping", state)
                try:
                    if not work.done():
                        result = await _drain(work, control)
                finally:
                    heartbeat_stop.set()
                    pulse.cancel()
                    stop_waiter.cancel()
                    await asyncio.gather(pulse, stop_waiter, return_exceptions=True)
    except ResourceCleanupError:
        reports.emit("fatal", state, reason=FailureReason.DEADLINE_EXCEEDED)
        result = 1
    except asyncio.CancelledError:
        raise
    except Exception:
        reports.emit("fatal", state, reason=FailureReason.UNEXPECTED_FAILURE)
        result = 1
    finally:
        control.request_stop()
        state.phase = "stopped"
        reports.emit("stopped", state)
        await reports.close(control.deadline)
    return result
