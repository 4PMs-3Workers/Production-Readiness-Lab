import asyncio
import json

import pytest
from sqlalchemy import text
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.worker.reporting import Reporter
from app.worker.runtime import run_worker

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("case", ["missing_table", "multiple_heads"])
async def test_incompatible_schema_stops_before_any_business_write(worker_env, case):
    env = worker_env
    await add_proposal(env)
    async with env.engine.begin() as connection:
        if case == "missing_table":
            await connection.execute(text("DROP TABLE alembic_version"))
        else:
            await connection.execute(text("INSERT INTO alembic_version VALUES ('unknown-head')"))
    output = []
    async with asyncio.timeout(2):
        code = await run_worker(
            env.settings,
            once=True,
            engine_factory=lambda *args, **kwargs: env.engine,
            reporter=Reporter(output.append),
        )
    assert code == 2
    records = [json.loads(line) for line in output]
    assert any(row.get("reason") == "SCHEMA_INCOMPATIBLE" for row in records)
    assert records[-1]["last_successful_scan"] is None and records[-1]["expired"] == 0
    assert await side_effect_counts(env) == (0, 0)
