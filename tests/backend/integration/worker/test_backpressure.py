import asyncio
import json
import subprocess
import sys
from contextlib import ExitStack

import pytest
from sqlalchemy import func, select
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts
from tests.backend.integration.worker.test_entrypoint import ROOT, invoke, worker_environment
from tests.backend.integration.worker.test_failures import assert_locks_released

from app.models import Appointment, ChangeProposal

pytestmark = pytest.mark.mysql

STOP_SCRIPT = """
import asyncio, json, os
from pathlib import Path
from app.core.config import load_settings
from app.core.database import create_database_engine
from app.worker import runtime
from app.worker.__main__ import configure_logging
from app.worker.reporting import Reporter

async def main():
    configure_logging()
    settings = load_settings()
    shutdown = runtime.Shutdown(settings.worker_grace_seconds)
    reports = Reporter()
    loop = asyncio.get_running_loop()
    requested = []
    pools = []
    original = runtime.process_candidate
    scheduled = False
    def stop():
        requested.append(loop.time())
        shutdown.request_stop()
    async def slow_candidate(*args, **kwargs):
        nonlocal scheduled
        await asyncio.sleep(0.05)
        result = await original(*args, **kwargs)
        if not scheduled:
            scheduled = True
            loop.call_later(0.1, stop)
        return result
    runtime.process_candidate = slow_candidate
    def engine_factory(*args, **kwargs):
        engine = create_database_engine(*args, **kwargs)
        pools.append(engine.pool)
        return engine
    code = await runtime.run_worker(
        settings, shutdown=shutdown, reporter=reports, engine_factory=engine_factory
    )
    assert requested and pools
    Path(os.environ["WORKER_TEST_RESULT_PATH"]).write_text(json.dumps({
        "exit_code": code,
        "stop_elapsed": loop.time() - requested[0],
        "connections_returned": all(pool.checkedout() == 0 for pool in pools),
        "reporting_failures": reports._state.reporting_failures,
    }), encoding="utf-8")
    return code
raise SystemExit(asyncio.run(main()))
"""


async def await_exit_without_reading(child: subprocess.Popen[bytes]) -> None:
    # Observation bound includes interpreter/DB startup; it is not an expiry SLA.
    async with asyncio.timeout(20):
        while child.poll() is None:
            await asyncio.sleep(0.01)


def collect_child(child: subprocess.Popen[bytes]) -> tuple[bytes | None, bytes | None]:
    if child.poll() is None:
        child.kill()
    return child.communicate(timeout=5)


async def assert_durable_bundle(env, total, *, partial=False):
    async with env.factory() as db:
        pending = await db.scalar(
            select(func.count())
            .select_from(ChangeProposal)
            .where(ChangeProposal.status == "PENDING")
        )
        expired = await db.scalar(
            select(func.count())
            .select_from(ChangeProposal)
            .where(ChangeProposal.status == "EXPIRED")
        )
        cancelled = await db.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.status == "CANCELLED")
        )
    assert pending is not None and expired is not None
    assert pending + expired == total and cancelled == expired
    assert (0 < pending < total) if partial else pending == 0
    assert await side_effect_counts(env) == (expired, expired * 2)
    await assert_locks_released(env)


@pytest.mark.parametrize("consumer", ["file", "unread-pipe"])
async def test_actual_cli_finishes_persisted_backlog_even_when_stdout_is_not_read(
    worker_env, tmp_path, consumer
):
    env = worker_env
    for _ in range(20):
        await add_proposal(env)
    environment = worker_environment(env)
    environment.update(
        PYTHONPATH=str(ROOT / "src/backend"),
        WORKER_BATCH_SIZE="100",
        WORKER_HEARTBEAT_INTERVAL_SECONDS="15",
        WORKER_POLL_INTERVAL_SECONDS="5",
        WORKER_GRACE_SECONDS="30",
    )
    with ExitStack() as stack:
        target = (
            stack.enter_context((tmp_path / "worker.jsonl").open("wb"))
            if consumer == "file"
            else subprocess.PIPE
        )
        child = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--once"],
            cwd=ROOT,
            env=environment,
            stdout=target,
            stderr=subprocess.PIPE,
            # Linux can explicitly shrink the pipe; Windows uses its small default.
            pipesize=4096 if sys.platform == "linux" else -1,
            text=False,
        )
        try:
            await await_exit_without_reading(child)
            # Inspect durable state before draining stdout; communicate() cannot hide a hang.
            assert child.returncode == 0
            await assert_durable_bundle(env, 20)
        finally:
            output, errors = collect_child(child)
    assert errors == b""
    if consumer == "file":
        records = [
            json.loads(line) for line in (tmp_path / "worker.jsonl").read_text().splitlines()
        ]
        assert records[-1]["expired"] == 20 and records[-1]["phase"] == "stopped"
    else:
        assert output is not None and 0 < len(output) <= 8192
        assert output.count(b'"event": "candidate"') < 20  # output was lost, business work was not


async def test_unread_pipe_allows_stop_callback_cleanup_and_recovery_with_backlog_remaining(
    worker_env, tmp_path
):
    env = worker_env
    for _ in range(20):
        await add_proposal(env)
    result_path = tmp_path / "stop-result.json"
    environment = worker_environment(env)
    environment.update(
        PYTHONPATH=str(ROOT / "src/backend"),
        WORKER_HEARTBEAT_INTERVAL_SECONDS="0.001",
        WORKER_GRACE_SECONDS="0.1",
        WORKER_TEST_RESULT_PATH=str(result_path),
    )
    child = subprocess.Popen(
        [sys.executable, "-c", STOP_SCRIPT],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pipesize=4096 if sys.platform == "linux" else -1,
        text=False,
    )
    try:
        await await_exit_without_reading(child)
        assert child.returncode == 0
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["exit_code"] == 0 and result["connections_returned"]
        assert result["stop_elapsed"] <= 0.25  # 0.1-second grace plus scheduling tolerance
        assert result["reporting_failures"] > 0
        await assert_durable_bundle(env, 20, partial=True)
    finally:
        _, errors = collect_child(child)
    assert errors == b""
    assert (await invoke(env, "--once"))[0] == 0
    await assert_durable_bundle(env, 20)
    assert (await invoke(env, "--once"))[0] == 0
    assert await side_effect_counts(env) == (20, 40)
