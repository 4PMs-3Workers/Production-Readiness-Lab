"""Opt-in VT-071 local ASGI + real MySQL load and order/isolation experiments."""

import asyncio
import json
import math
import os
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import httpx
import pytest
from sqlalchemy import text
from tests.backend.integration.test_appointment_export import PATH, rows, seed_history
from tests.backend.integration.test_appointments_api import ORIGIN
from tests.backend.integration.test_booking_context import sign_in

pytestmark = pytest.mark.skipif(
    os.getenv("BT3_RUN_LOAD") != "1", reason="Opt in with BT3_RUN_LOAD=1 (80-task experiment)"
)


async def test_export_80_tasks_five_rounds(availability_env):
    env = availability_env
    await seed_history(env, 100)
    await sign_in(env)
    expected_ids = {row["id"] for row in rows(await env.client.get(PATH))}
    assert len(expected_ids) == 100
    async with env.factory() as db:
        mysql_version = await db.scalar(text("SELECT VERSION()"))
    measurements = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "versions": {p: version(p) for p in ["fastapi", "SQLAlchemy", "asyncmy", "pytest"]},
        "mysql": mysql_version,
        "transport": "httpx ASGITransport; real TCP MySQL; no gateway/TLS",
        "concurrency": 80,
        "rounds": [],
        "owned_rows": 100,
        "foreign_rows": 1,
        "sessions": "80 independent clients copying one real STUDENT session cookie",
        "target_p95_seconds": 2.5,
        "percentile": "nearest-rank ceil(0.95*n)-1",
        "timeouts": {
            "request": env.app.state.settings.request_timeout_seconds,
            "db_query": env.app.state.settings.db_query_timeout_seconds,
        },
    }
    clients = [
        httpx.AsyncClient(
            transport=httpx.ASGITransport(env.app), base_url=ORIGIN, cookies=env.client.cookies
        )
        for _ in range(80)
    ]
    try:
        for round_number in range(1, 6):
            barrier = asyncio.Barrier(80)

            async def download(client, barrier=barrier):
                await barrier.wait()
                started = perf_counter()
                try:
                    response = await client.get(PATH)
                    duration = perf_counter() - started
                    exported = rows(response)
                    assert {row["id"] for row in exported} == expected_ids
                    return {
                        "latency_seconds": duration,
                        "status": response.status_code,
                        "scope_ok": True,
                    }
                except Exception as error:
                    return {
                        "latency_seconds": perf_counter() - started,
                        "error": type(error).__name__,
                        "scope_ok": False,
                    }

            started = perf_counter()
            samples = await asyncio.wait_for(
                asyncio.gather(*(download(client) for client in clients)), timeout=30
            )
            latencies = sorted(sample["latency_seconds"] for sample in samples)
            measurements["rounds"].append(
                {
                    "round": round_number,
                    "samples": samples,
                    "wall_seconds": perf_counter() - started,
                    "p50_seconds": latencies[math.ceil(0.50 * 80) - 1],
                    "p95_seconds": latencies[math.ceil(0.95 * 80) - 1],
                    "max_seconds": latencies[-1],
                    "errors": sum(not sample["scope_ok"] for sample in samples),
                }
            )
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    evidence = Path("docs/assignments/VT-071/evidence/performance.json")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(measurements, indent=2), encoding="utf-8")
    for result in measurements["rounds"]:
        print(json.dumps({k: v for k, v in result.items() if k != "samples"}))
    assert all(result["errors"] == 0 for result in measurements["rounds"])
    assert all(result["p95_seconds"] <= 2.5 for result in measurements["rounds"])
