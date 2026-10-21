import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

pytestmark = pytest.mark.mysql
ROOT = Path(__file__).resolve().parents[4]


def worker_environment(env):
    return dict(
        os.environ,
        **{
            "DATABASE_URL": env.engine.url.render_as_string(hide_password=False),
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "test-only-worker-cookie-secret",
            "CSRF_SECRET": "test-only-worker-csrf-secret",
            "COOKIE_SECURE": "false",
            "ENVIRONMENT": "test",
            "ACCEPTED_SCHEMA_REVISIONS": "20261019_1200_proposal_replay",
            "WORKER_POLL_INTERVAL_SECONDS": "0.05",
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.05",
            "WORKER_GRACE_SECONDS": "1",
        },
    )


async def invoke(env, *args):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        *args,
        cwd=ROOT,
        env=worker_environment(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            output, errors = await process.communicate()
        return process.returncode, output.decode(), errors.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def test_installed_module_processes_one_batch_and_restart_has_no_duplicate_effects(
    worker_env,
):
    env = worker_env
    await add_proposal(env)
    worker_ids = []
    for expected_expired in (1, 0):
        code, output, errors = await invoke(env, "--once")
        assert code == 0, errors
        records = [json.loads(line) for line in output.splitlines()]
        assert records[-1]["event"] == "stopped" and records[-1]["expired"] == expected_expired
        assert records[-1]["successful_scans"] == 1
        worker_ids.append(records[-1]["worker_id"])
        assert "private-worker-fixture" not in output + errors
        assert await side_effect_counts(env) == (1, 2)
    assert worker_ids[0] != worker_ids[1]
