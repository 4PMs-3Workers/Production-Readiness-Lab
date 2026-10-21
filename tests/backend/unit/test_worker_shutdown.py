import asyncio
import json
from unittest.mock import AsyncMock, Mock

from app.worker.reporting import Reporter
from app.worker.runtime import Shutdown, run_worker


async def test_repeated_stop_does_not_extend_the_grace_deadline():
    shutdown = Shutdown(0.1)
    shutdown.request_stop()
    deadline = shutdown.deadline
    shutdown.request_stop()
    assert shutdown.stop.is_set() and shutdown.deadline == deadline


async def test_pre_stopped_worker_creates_no_database_resources(auth_settings):
    shutdown = Shutdown(1)
    shutdown.request_stop()
    create = Mock()
    assert await run_worker(auth_settings, shutdown=shutdown, engine_factory=create) == 0
    create.assert_not_called()


async def test_cleanup_has_no_second_grace_period_and_detaches_a_stuck_pool(
    auth_settings, monkeypatch
):
    from app.worker import runtime

    settings = auth_settings.model_copy(update={"worker_grace_seconds": 0.1})
    engine = Mock()
    dispose_started = asyncio.Event()

    async def blocked_dispose():
        dispose_started.set()
        await asyncio.Event().wait()

    engine.dispose = AsyncMock(side_effect=blocked_dispose)
    engine.sync_engine.dispose = Mock()

    async def completed_poll(*args, **kwargs):
        return 0

    monkeypatch.setattr(runtime, "poll", completed_poll)
    output = []
    async with asyncio.timeout(1):
        result = await run_worker(
            settings, engine_factory=Mock(return_value=engine), reporter=Reporter(output.append)
        )
    assert result == 1 and dispose_started.is_set()
    engine.sync_engine.dispose.assert_called_once_with(close=False)
    events = [json.loads(line) for line in output]
    assert events[-1]["phase"] == "stopped"
    assert any(row.get("reason") == "DEADLINE_EXCEEDED" for row in events)
