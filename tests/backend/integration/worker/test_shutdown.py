import asyncio
import json

import pytest
from sqlalchemy import text
from tests.backend.integration.test_cancellation_concurrency import wait_for_lock
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts
from tests.backend.integration.worker.test_failures import assert_locks_released

from app.repositories import users
from app.worker.reporting import Reporter
from app.worker.runtime import Shutdown, run_worker

pytestmark = pytest.mark.mysql


async def test_shutdown_cancels_locked_work_within_grace_and_preserves_pending_backlog(
    worker_env, monkeypatch
):
    env = worker_env
    await add_proposal(env)
    settings = env.settings.model_copy(
        update={
            "worker_grace_seconds": 0.2,
            "request_timeout_seconds": 10,
            "db_query_timeout_seconds": 8,
            "db_lock_timeout_seconds": 5,
        }
    )
    shutdown = Shutdown(settings.worker_grace_seconds)
    waiting = asyncio.Event()
    ids = []
    output = []
    original = users.lock_by_id
    async with env.factory.begin() as blocker:
        await original(blocker, env.staff_id)

        async def tracked(db, account_id):
            if account_id == env.staff_id:
                ids.append(await db.scalar(text("SELECT CONNECTION_ID()")))
                waiting.set()
            return await original(db, account_id)

        with monkeypatch.context() as patch:
            patch.setattr(users, "lock_by_id", tracked)
            task = asyncio.create_task(
                run_worker(
                    settings,
                    shutdown=shutdown,
                    engine_factory=lambda *args, **kwargs: env.engine,
                    reporter=Reporter(output.append),
                )
            )
            try:
                async with asyncio.timeout(2):
                    await waiting.wait()
                    async with env.factory() as observer:
                        await wait_for_lock(observer, ids[0])
                shutdown.request_stop()
                async with asyncio.timeout(1):
                    assert await task == 0
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert await side_effect_counts(env) == (0, 0)
    await assert_locks_released(env)
    assert json.loads(output[-1])["phase"] == "stopped"
