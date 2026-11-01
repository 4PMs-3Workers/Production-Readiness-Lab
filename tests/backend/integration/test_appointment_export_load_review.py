"""Opt-in cold/warm bursts with sanitized instrumentation inside the server call path.

No production telemetry behavior or timeout is changed. This is diagnostic harness
instrumentation around the actual auth/export services, not an HTTP response mock.
"""

import asyncio
import json
import math
import os
import platform
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import perf_counter

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tests.backend.integration.test_appointment_export import PATH, rows, seed_history
from tests.backend.integration.test_appointments_api import ORIGIN
from tests.backend.integration.test_booking_context import sign_in

from app.services import appointment_export, auth

pytestmark = pytest.mark.skipif(os.getenv("BT3_RUN_LOAD") != "1", reason="BT3_RUN_LOAD=1 required")
SAMPLE: ContextVar[dict | None] = ContextVar("vt071_sample", default=None)


def exception_types_and_codes(error):
    """Finite type names and integer codes only: no str(error), traceback, SQL or tokens."""
    pending = [error]
    seen = set()
    chain = []
    while pending and len(chain) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        code = getattr(current, "errno", None)
        if not isinstance(code, int) or isinstance(code, bool):
            args = getattr(current, "args", ())
            code = (
                args[0]
                if args and isinstance(args[0], int) and not isinstance(args[0], bool)
                else None
            )
        chain.append({"exception_type": type(current).__name__, "db_code": code})
        pending.extend(
            nested
            for nested in (getattr(current, "orig", None), current.__cause__, current.__context__)
            if isinstance(nested, BaseException)
        )
    return chain


def instrument(stage, original):
    @wraps(original)
    async def measured(*args, **kwargs):
        record = SAMPLE.get()
        started = perf_counter()
        try:
            return await original(*args, **kwargs)
        except BaseException as error:
            if record is not None:
                failure = {
                    "event": "vt071.server.failure",
                    "stage": stage,
                    "sample": record["sample"],
                    "chain": exception_types_and_codes(error),
                }
                record.setdefault("server_failures", []).append(failure)
                print(json.dumps(failure))
            raise
        finally:
            if record is not None:
                record[stage + "_seconds"] = perf_counter() - started

    return measured


class DiagnosticApp:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)
        # The client task sets this context. The server's stages append to the same
        # per-task record; concurrent requests never use one global mutable record.
        await self.application(scope, receive, send)
        record = SAMPLE.get()
        if record is not None:
            record["request_id"] = scope.get("state", {}).get("request_id")


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else None


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("PRIVATE_DIAGNOSTIC_CANARY"),
        OperationalError(
            "PRIVATE_DIAGNOSTIC_CANARY", {}, Exception(1054, "PRIVATE_DIAGNOSTIC_CANARY")
        ),
    ],
)
async def test_server_diagnostics_omit_exception_messages(error):
    """Deterministic probe validates the logger; it does not explain historical 503s."""
    record = {"sample": "diagnostic-probe", "server_failures": []}
    token = SAMPLE.set(record)

    async def failure():
        raise error

    try:
        with pytest.raises(type(error)):
            await instrument("export", failure)()
    finally:
        SAMPLE.reset(token)
    serialized = json.dumps(record)
    assert "PRIVATE_DIAGNOSTIC_CANARY" not in serialized
    assert record["server_failures"][0]["chain"][0]["exception_type"] == type(error).__name__
    if isinstance(error, OperationalError):
        assert any(item["db_code"] == 1054 for item in record["server_failures"][0]["chain"])


async def test_export_cold_full_warmup_and_warm_bursts(availability_env, monkeypatch):
    env = availability_env
    await seed_history(env, 100)
    await sign_in(env)
    # Preserve old experiment's single warmup, then measure a first full burst.
    expected = {row["id"] for row in rows(await env.client.get(PATH))}
    assert len(expected) == 100
    async with env.factory() as db:
        mysql_version = await db.scalar(text("SELECT VERSION()"))
    monkeypatch.setattr(auth, "authenticate", instrument("auth", auth.authenticate))
    monkeypatch.setattr(
        appointment_export,
        "export_student_appointments",
        instrument("export", appointment_export.export_student_appointments),
    )
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "mysql": mysql_version,
        "concurrency": 80,
        "owned_rows": 100,
        "foreign_rows": 1,
        "engine": "NullPool, READ COMMITTED",
        "transport": "ASGI plus real MySQL TCP",
        "sessions": "80 clients sharing one real student session; no session UPDATE on read",
        "timeouts": {
            "request": env.app.state.settings.request_timeout_seconds,
            "db_query": env.app.state.settings.db_query_timeout_seconds,
        },
        "cold_definition": "first 80-task burst after one serial request; not a restarted MySQL",
        "instrumentation": "test-only wrappers in server auth/export; type/code, no error messages",
        "bursts": [],
    }
    clients = [
        httpx.AsyncClient(
            transport=httpx.ASGITransport(DiagnosticApp(env.app)),
            base_url=ORIGIN,
            cookies=env.client.cookies,
        )
        for _ in range(80)
    ]
    try:
        for phase in ["cold", "warmup80", "warm1", "warm2", "warm3", "warm4", "warm5"]:
            barrier = asyncio.Barrier(80)

            async def download(index, client, barrier=barrier, phase=phase):
                record = {"sample": f"{phase}-{index}", "server_failures": []}
                token = SAMPLE.set(record)
                response = None
                await barrier.wait()
                started = perf_counter()
                try:
                    response = await client.get(PATH)
                    record.update(
                        latency_seconds=perf_counter() - started, status=response.status_code
                    )
                    exported = rows(response)
                    record["exported_count"] = len(exported)
                    record["scope_ok"] = (
                        len(exported) == len(expected)
                        and {row["id"] for row in exported} == expected
                    )
                except Exception as error:
                    record.setdefault("latency_seconds", perf_counter() - started)
                    record["status"] = response.status_code if response is not None else None
                    record["scope_ok"] = False
                    record["client_exception_type"] = type(error).__name__
                finally:
                    SAMPLE.reset(token)
                return record

            samples = await asyncio.wait_for(
                asyncio.gather(*(download(index, client) for index, client in enumerate(clients))),
                timeout=30,
            )
            good = [s["latency_seconds"] for s in samples if s["scope_ok"]]
            failures = [f for s in samples for f in s["server_failures"]]
            summary = {
                "phase": phase,
                "samples": samples,
                "p95_all_seconds": p95([s["latency_seconds"] for s in samples]),
                "p95_success_seconds": p95(good),
                "errors": sum(not s["scope_ok"] for s in samples),
                "status_counts": dict(Counter(str(s["status"]) for s in samples)),
                "failure_stages": dict(Counter(f["stage"] for f in failures)),
                "exception_types": dict(
                    Counter(e["exception_type"] for f in failures for e in f["chain"])
                ),
            }
            report["bursts"].append(summary)
            print(json.dumps({k: v for k, v in summary.items() if k != "samples"}))
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    report["cold_gate"] = (
        report["bursts"][0]["errors"] == 0 and report["bursts"][0]["p95_success_seconds"] <= 2.5
    )
    report["warm_gate"] = all(
        b["errors"] == 0 and b["p95_success_seconds"] <= 2.5
        for b in report["bursts"]
        if b["phase"].startswith("warm") and b["phase"] != "warmup80"
    )
    Path("docs/assignments/VT-071/evidence/review-performance.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    assert (
        report["cold_gate"] and report["warm_gate"]
    ), "Cold/warm acceptance gate failed; raw evidence retained"
