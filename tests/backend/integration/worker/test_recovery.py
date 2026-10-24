import asyncio
import json
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import QueuePool
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts
from tests.backend.integration.worker.test_entrypoint import ROOT, invoke, worker_environment
from tests.backend.integration.worker.test_failures import assert_locks_released

from app.core.database import create_database_engine
from app.repositories import appointments, users
from app.worker.batch import BatchRunner, ScanFailure
from app.worker.models import FailureReason, Outcome
from app.worker.processing import process_candidate

pytestmark = pytest.mark.mysql

CRASH_SCRIPT = """
import os
from app.worker import processing
from app.worker.__main__ import main
original_commit = processing.commit_safely
async def interrupted_commit(db, transaction, **kwargs):
    if os.environ["WORKER_TEST_CRASH_PHASE"] == "after":
        await original_commit(db, transaction, **kwargs)
    os._exit(19)
processing.commit_safely = interrupted_commit
raise SystemExit(main(["--once"]))
"""


@pytest.mark.parametrize("operation", ["scan", "candidate"])
async def test_exhausted_pool_times_out_without_leaks_then_recovers(
    worker_env, operation, worker_reports
):
    env = worker_env
    identifier, _ = await add_proposal(env)
    engine = create_database_engine(
        env.engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.05,
        connect_args={"connect_timeout": 1},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pool = engine.pool
    assert isinstance(pool, QueuePool)
    runner = BatchRunner(factory, env.settings, reporter=worker_reports())
    try:
        async with engine.connect() as blocker:
            await blocker.execute(text("SELECT 1"))
            async with asyncio.timeout(1):
                if operation == "scan":
                    with pytest.raises(ScanFailure):
                        await runner.run_batch()
                    assert runner.state.failed_scans == 1 and runner.state.successful_scans == 0
                else:
                    result = await process_candidate(
                        factory, identifier, env.settings, "worker.pool"
                    )
                    assert (
                        result.reason == FailureReason.DATABASE_UNAVAILABLE and result.attempts == 1
                    )
            assert pool.checkedout() == 1
        assert pool.checkedout() == 0 and await side_effect_counts(env) == (0, 0)
        result = await process_candidate(factory, identifier, env.settings, "worker.pool-recovered")
        assert result.outcome == Outcome.EXPIRED and pool.checkedout() == 0
        assert await side_effect_counts(env) == (1, 2)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("phase", ["before", "after"])
async def test_hard_process_crash_before_or_after_commit_recovers_without_duplicate_effects(
    worker_env, phase
):
    env = worker_env
    await add_proposal(env)
    environment = worker_environment(env)
    environment["WORKER_TEST_CRASH_PHASE"] = phase
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CRASH_SCRIPT,
        cwd=ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            output, errors = await child.communicate()
        assert child.returncode == 19, errors.decode()
        records = [json.loads(line) for line in output.decode().splitlines()]
        assert not any(row["event"] == "candidate" for row in records)  # no acknowledgement
    finally:
        if child.returncode is None:
            child.kill()
            await child.communicate()
    await assert_locks_released(env)
    assert await side_effect_counts(env) == ((1, 2) if phase == "after" else (0, 0))
    code, output, errors = await invoke(env, "--once")
    assert code == 0, errors
    assert json.loads(output.splitlines()[-1])["expired"] == (0 if phase == "after" else 1)
    assert await side_effect_counts(env) == (1, 2)


async def test_stopped_worker_backlog_demonstration(worker_env):
    """Repeatable manual demo: run with pytest -s -k stopped_worker_backlog_demonstration."""
    env = worker_env
    async with env.factory() as db:
        expiry = await users.utc_now(db) + timedelta(seconds=10)
    proposal_id, _ = await add_proposal(env, expiry=expiry)
    print(f"Created pending proposal {proposal_id}; expiry UTC {expiry.isoformat()}")
    code, output, errors = await invoke(env, "--once")
    assert code == 0, errors
    assert json.loads(output.splitlines()[-1])["expired"] == 0
    print("Worker stopped before expiry; database row is the durable queue.")
    async with env.factory() as db:
        async with asyncio.timeout(15):
            while await users.utc_now(db) < expiry:
                await asyncio.sleep(0.05)
        assert await appointments.count_blocking_for_student(db, student_id=env.student_id) == 1
    assert await side_effect_counts(env) == (0, 0)
    print("Expiry passed while stopped; appointment still occupies quota and its original slot.")
    code, output, errors = await invoke(env, "--once")
    assert code == 0, errors
    assert json.loads(output.splitlines()[-1])["expired"] == 1
    assert await side_effect_counts(env) == (1, 2)
    code, _, errors = await invoke(env, "--once")
    assert code == 0, errors
    assert await side_effect_counts(env) == (1, 2)
    print("Restart verified: one expiry audit, two notifications; another restart adds no effects.")


async def test_cli_database_failure_publishes_safe_failed_scan_not_empty_success(worker_env):
    env = worker_env
    denied_url = env.engine.url.set(
        username="worker-denied-" + uuid4().hex[:12], password="private-denied-password"
    )
    code, output, errors = await invoke(
        env,
        "--once",
        env_overrides={"DATABASE_URL": denied_url.render_as_string(hide_password=False)},
    )
    assert code == 1
    records = [json.loads(line) for line in output.splitlines()]
    final = records[-1]
    assert final["failed_scans"] == 1 and final["successful_scans"] == 0
    assert final["last_successful_scan"] is None and final["database_ok"] is False
    assert "private-denied-password" not in output + errors
    assert "traceback" not in (output + errors).lower()
    assert "SELECT version_num" not in output + errors
    assert await side_effect_counts(env) == (0, 0)
