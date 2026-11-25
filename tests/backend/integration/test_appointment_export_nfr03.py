"""Opt-in pool comparison; all profiling is test-only and changes no deadline.

Explicit session.connection() separates checkout from execute in the diagnostic
path. AsyncSession.execute buffers driver rows: its timing includes wire fetch;
Result.all timing below measures only extraction of those already-buffered rows.
"""

import asyncio
import ctypes
import hashlib
import json
import os
import platform
import subprocess
from collections import Counter
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from importlib.metadata import version
from pathlib import Path
from time import perf_counter, process_time
from uuid import UUID

import httpx
import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from sqlalchemy.pool import NullPool
from tests.backend.integration.nfr03_native import native_api
from tests.backend.integration.test_appointment_export import PATH, rows, seed_history
from tests.backend.integration.test_appointment_export_load_review import (
    SAMPLE,
    DiagnosticApp,
    exception_types_and_codes,
    instrument,
    p95,
)
from tests.backend.integration.test_appointments_api import ORIGIN
from tests.backend.integration.test_booking_context import sign_in
from tools.deploy.release_manifest import SCHEMA
from tools.load.export_probe import PHASES, burst_verdict, measure_pool, save_report

from app.core.config import Settings
from app.core.database import create_database_engine
from app.factory import create_app
from app.services import appointment_export, auth

pytestmark = [
    pytest.mark.load,
    pytest.mark.skipif(os.getenv("BT3_RUN_LOAD") != "1", reason="BT3_RUN_LOAD=1 required"),
]
STAGE = ContextVar("vt071_pool_stage", default=None)

TIMINGS = ["connect_checkout", "execute_buffered", "fetch_buffered", "map", "rollback", "format"]


def timed(stage, original):
    @wraps(original)
    async def measured(*args, **kwargs):
        record = SAMPLE.get()
        if record is None or STAGE.get() != "export":
            return await original(*args, **kwargs)
        started = perf_counter()
        try:
            return await original(*args, **kwargs)
        except BaseException as error:
            record.setdefault("operation_failures", []).append(
                {"operation": stage, "chain": exception_types_and_codes(error)}
            )
            raise
        finally:
            key = stage + "_seconds"
            record[key] = record.get(key, 0) + perf_counter() - started

    return measured


def timed_sync(stage, original):
    @wraps(original)
    def measured(*args, **kwargs):
        record = SAMPLE.get()
        started = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            if record is not None and STAGE.get() == "export":
                key = stage + "_seconds"
                record[key] = record.get(key, 0) + perf_counter() - started

    return measured


def install_profiling(monkeypatch):
    original_transaction = appointment_export.read_transaction
    original_execute = AsyncSession.execute

    @asynccontextmanager
    async def transaction(db, settings, **kwargs):
        async with original_transaction(db, settings, **kwargs):
            # Same transaction, explicit checkout before the repository SELECT.
            await timed("connect_checkout", db.connection)()
            yield

    class BufferedResult:
        def __init__(self, result):
            self.result = result

        def all(self):
            return timed_sync("fetch_buffered", self.result.all)()

    async def execute(db, *args, **kwargs):
        record = SAMPLE.get()
        label = STAGE.get()
        if (
            record is not None
            and label in {"auth", "export"}
            and not record.get(label + "_checkouts")
        ):
            record.setdefault("_acquire_started", perf_counter())
        try:
            result = await timed("execute_buffered", original_execute)(db, *args, **kwargs)
        except BaseException as error:
            record = SAMPLE.get()
            if record is not None and STAGE.get() == "auth":
                record.setdefault("operation_failures", []).append(
                    {
                        "operation": "auth_execute",
                        "exception_module": type(error).__module__,
                        "pool_queue_timeout": isinstance(error, PoolTimeoutError),
                        "chain": exception_types_and_codes(error),
                    }
                )
            raise
        return BufferedResult(result) if STAGE.get() == "export" else result

    def staged(name, original):
        measured = instrument(name, original)

        @wraps(original)
        async def call(*args, **kwargs):
            token = STAGE.set(name)
            try:
                return await measured(*args, **kwargs)
            finally:
                STAGE.reset(token)

        return call

    monkeypatch.setattr(appointment_export, "read_transaction", transaction)
    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(
        AsyncSessionTransaction, "rollback", timed("rollback", AsyncSessionTransaction.rollback)
    )
    for name, stage in [("_list_item", "map"), ("render_csv", "format")]:
        monkeypatch.setattr(
            appointment_export, name, timed_sync(stage, getattr(appointment_export, name))
        )
    monkeypatch.setattr(auth, "authenticate", staged("auth", auth.authenticate))
    monkeypatch.setattr(
        appointment_export,
        "export_student_appointments",
        staged("export", appointment_export.export_student_appointments),
    )


def host_cpu_ticks():
    if os.name != "nt":
        return None
    idle, kernel, user = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    return idle.value, kernel.value + user.value  # Windows kernel time includes idle.


async def mysql_snapshot(connection, schema):
    status = dict(
        (
            await connection.execute(
                text(
                    "SHOW GLOBAL STATUS WHERE Variable_name IN "
                    "('Threads_running','Threads_connected','Aborted_clients','Connections','Uptime')"
                )
            )
        ).all()
    )
    # Do not record INFO, user, host, SQL, tokens or exception messages.
    processes = (
        (
            await connection.execute(
                text(
                    "SELECT ID, DB, COMMAND, TIME, STATE FROM information_schema.PROCESSLIST "
                    "WHERE ID <> CONNECTION_ID()"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "status": {key: int(value) for key, value in status.items()},
        "owned_schema_processes": [
            {key.lower(): row[key] for key in ("ID", "COMMAND", "TIME", "STATE")}
            for row in processes
            if row["DB"] == schema
        ],
    }


@pytest.mark.parametrize("pool_mode", ["production"])
async def test_export_nfr03(availability_env, mysql_engine, monkeypatch, pool_mode):
    env = availability_env
    await seed_history(env, 100)
    await sign_in(env)
    expected = {str(UUID(int=100 + i)) for i in range(100)}
    assert {row["id"] for row in rows(await env.client.get(PATH))} == expected
    assert len(expected) == 100
    settings = env.app.state.settings.model_copy(
        update={
            "database_url": mysql_engine.url.render_as_string(hide_password=False),
            "accepted_schema_revisions_raw": SCHEMA,
        }
    )
    assert (settings.db_pool_size, settings.db_max_overflow, settings.db_pool_timeout_seconds) == (
        5,
        5,
        0.5,
    )

    profile = os.getenv("NFR03_PROFILE", "default")
    profiles = {
        "default": (5, 5),
        "retain10": (10, 0),
        "capacity20": (20, 0),
        "capacity40": (40, 0),
        "candidate40": (40, 0),
    }
    assert profile in profiles
    size, overflow = profiles[profile]
    queue_timeout = 0.75 if profile == "candidate40" else 0.5
    settings = settings.model_copy(
        update={
            "db_pool_size": size,
            "db_max_overflow": overflow,
            "db_pool_timeout_seconds": queue_timeout,
        }
    )

    if profile == "candidate40":
        overrides = dotenv_values("tools/deploy/profiles/nfr03-candidate.env.example")
        settings = Settings.model_validate({**settings.model_dump(by_alias=True), **overrides})
        size, overflow = settings.db_pool_size, settings.db_max_overflow

    def null_factory(url, *, connect_args, **_pool_options):
        return create_database_engine(url, connect_args=connect_args, poolclass=NullPool)

    application = (
        create_app(settings, engine_factory=null_factory)
        if pool_mode == "null"
        else create_app(settings)
    )
    monitor = create_database_engine(mysql_engine.url.set(database=None), poolclass=NullPool)
    native = os.getenv("NFR03_TRANSPORT", "asgi") == "http"
    diagnostic = not native and os.getenv("NFR03_DIAGNOSTICS", "1") == "1"
    if diagnostic:
        install_profiling(monkeypatch)
    report = {
        "protocol": "nfr03-export-v2",
        "diagnostics": diagnostic,
        "server_diagnostics": os.getenv("NFR03_SERVER_DIAGNOSTICS") == "1",
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
        "versions": {p: version(p) for p in ["SQLAlchemy", "asyncmy", "fastapi"]},
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "pool_mode": pool_mode,
        "profile": profile,
        "pool_size": size,
        "max_overflow": overflow,
        "pool_timeout_seconds": settings.db_pool_timeout_seconds,
        "connect_timeout_seconds": settings.db_connect_timeout_seconds,
        "query_timeout_seconds": settings.db_query_timeout_seconds,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "concurrency": 80,
        "owned_rows": 100,
        "foreign_rows": 1,
        "transport": "ASGI plus real MySQL TCP; 80 clients share one real session",
        "measurement_limits": [
            "New diagnostic protocol, not the unchanged historical harness",
            "Explicit checkout inside read_transaction; execute includes buffered wire fetch",
            "fetch_buffered is Result.all extraction, not network fetch",
            "Monitor samples before/during/after each burst on the same event loop",
            "Processlist omits the observer itself; no query text is recorded",
            "Host CPU is all-core interval average; process CPU seconds concern the harness only",
            "First burst follows migrations, seed, login and serial request; not empty-cache cold",
        ],
        "bursts": [],
        "external_timing_limits": (
            "Native stage timers absent unless server diagnostics enabled; "
            "no gateway/TLS or worker load"
        ),
    }
    if native:
        report["transport"] = (
            "loopback HTTP, separate Uvicorn process, real MySQL TCP; no gateway/TLS"
        )
    completed = False
    try:
        if native:
            async with native_api(settings) as target, monitor.connect() as observer:
                await run_bursts(
                    application,
                    observer,
                    report,
                    settings,
                    pool_mode,
                    expected,
                    env,
                    mysql_engine,
                    target=target,
                )
        else:
            await run_asgi(
                application,
                monitor,
                report,
                settings,
                pool_mode,
                expected,
                env,
                mysql_engine,
                diagnostic,
            )
        completed = True
    finally:
        await finish_report(monitor, report, completed)
    assert report["passed"], "NFR-03 gate failed; evidence retained"


async def run_asgi(
    application, monitor, report, settings, pool_mode, expected, env, mysql_engine, diagnostic
):
    async with application.router.lifespan_context(application), monitor.connect() as observer:
        profiling = (
            measure_pool(application.state.engine, SAMPLE, STAGE) if diagnostic else nullcontext({})
        )
        with profiling as pool_metrics:
            report["pool_metrics"] = pool_metrics
            await run_bursts(
                application, observer, report, settings, pool_mode, expected, env, mysql_engine
            )


async def finish_report(monitor, report, completed):
    await monitor.dispose()
    report["completed"] = completed
    report["generator_saturated"] = any(
        b.get("event_loop_lag_samples", 0) < 2
        or b.get("event_loop_lag_p95_seconds") is None
        or b["event_loop_lag_p95_seconds"] > 0.1
        or b.get("lag_missed_tick_fraction", 1) > 0.1
        for b in report["bursts"]
    )
    report["passed"] = (
        completed and burst_verdict(report["bursts"]) and not report["generator_saturated"]
    )
    for burst in report["bursts"]:
        for sample in burst["samples"]:
            sample.pop("_acquire_started", None)
    report["source_file_sha256"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in [
            "src/backend/app/services/auth.py",
            "src/backend/app/services/appointment_export.py",
            "tools/load/export_probe.py",
            "tests/backend/integration/nfr03_native.py",
            "tools/deploy/profiles/nfr03-candidate.env.example",
            "tests/backend/integration/test_appointment_export_nfr03.py",
        ]
    }
    output = save_report(report, Path("build/nfr03/nfr03-export-v2") / report["source_sha"])
    print("NFR03_REPORT=" + str(output))


async def run_bursts(
    application, observer, report, settings, pool_mode, expected, env, mysql_engine, target=None
):
    report["engine_class"] = (
        "external process; not sampled" if target else type(application.state.engine.pool).__name__
    )
    report["mysql"] = await observer.scalar(text("SELECT VERSION()"))
    report["mysql_max_connections"] = await observer.scalar(text("SELECT @@max_connections"))
    clients = [
        httpx.AsyncClient(
            transport=(
                httpx.AsyncHTTPTransport(retries=0)
                if target
                else httpx.ASGITransport(DiagnosticApp(application))
            ),
            base_url=target or ORIGIN,
            cookies=dict(env.client.cookies) if target else env.client.cookies,
            trust_env=False,
        )
        for _ in range(80)
    ]
    try:
        assert len(rows(await clients[0].get(PATH))) == 100
        for phase in PHASES:
            snapshots = [await mysql_snapshot(observer, mysql_engine.url.database)]
            barrier = asyncio.Barrier(80)
            burst_clock = {}
            cpu_before, process_before, started = (
                host_cpu_ticks(),
                process_time(),
                perf_counter(),
            )
            stop = asyncio.Event()
            lag = []

            async def observe(stop=stop, lag=lag, snapshots=snapshots):
                while not stop.is_set():
                    target = perf_counter() + 0.25
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.25)
                        break
                    except TimeoutError:
                        pass
                    lag.append(max(0, perf_counter() - target))
                    snapshots.append(await mysql_snapshot(observer, mysql_engine.url.database))
                    if pool_mode == "production" and not target:
                        pool = application.state.engine.pool
                        snapshots[-1]["pool_checked_out"] = pool.checkedout()

            async def download(
                index, client, phase=phase, barrier=barrier, burst_clock=burst_clock
            ):
                record = {"sample": f"{phase}-{index}", "server_failures": []}
                token = SAMPLE.set(record)
                await barrier.wait()
                request_started = burst_clock.setdefault("released", perf_counter())
                record["send_skew_seconds"] = perf_counter() - request_started
                try:
                    response = await client.get(PATH)
                    record.update(
                        status=response.status_code,
                        latency_seconds=perf_counter() - request_started,
                    )
                    exported = rows(response)
                    record["exported_count"] = len(exported)
                    record["scope_ok"] = len(exported) == 100 and [r["id"] for r in exported] == [
                        str(UUID(int=100 + i)) for i in reversed(range(100))
                    ]
                except Exception as error:
                    record.setdefault("latency_seconds", perf_counter() - request_started)
                    record["client_exception_type"] = type(error).__name__
                    record.setdefault("status", None)
                    record["scope_ok"] = False
                finally:
                    SAMPLE.reset(token)
                return record

            watcher = asyncio.create_task(observe())
            try:
                samples = await asyncio.wait_for(
                    asyncio.gather(
                        *(download(index, client) for index, client in enumerate(clients))
                    ),
                    timeout=30,
                )
            finally:
                stop.set()
                await watcher
            elapsed, process_cpu, cpu_after = (
                perf_counter() - started,
                process_time() - process_before,
                host_cpu_ticks(),
            )
            snapshots.append(await mysql_snapshot(observer, mysql_engine.url.database))
            host_cpu = None
            if cpu_before and cpu_after and cpu_after[1] > cpu_before[1]:
                host_cpu = 100 * (
                    1 - (cpu_after[0] - cpu_before[0]) / (cpu_after[1] - cpu_before[1])
                )
            good = [s["latency_seconds"] for s in samples if s["scope_ok"]]
            summary = {
                "phase": phase,
                "samples": samples,
                "mysql_snapshots": snapshots,
                "errors": sum(not s["scope_ok"] for s in samples),
                "status_counts": dict(Counter(str(s["status"]) for s in samples)),
                "p95_all_seconds": p95([s["latency_seconds"] for s in samples]),
                "p95_success_seconds": p95(good),
                "timing_p95_seconds": {
                    key: p95([s[key + "_seconds"] for s in samples if key + "_seconds" in s])
                    for key in TIMINGS
                },
                "operation_failures": dict(
                    Counter(
                        f["operation"] for s in samples for f in s.get("operation_failures", [])
                    )
                ),
                "auth_pool_queue_timeouts": sum(
                    f.get("pool_queue_timeout", False)
                    for sample in samples
                    for f in sample.get("operation_failures", [])
                ),
                "elapsed_seconds": elapsed,
                "host_busy_percent": host_cpu,
                "harness_cpu_seconds": process_cpu,
                "event_loop_lag_p95_seconds": p95(lag),
                "event_loop_lag_samples": len(lag),
                "lag_missed_tick_fraction": sum(x > 0.1 for x in lag) / len(lag) if lag else None,
            }
            report["bursts"].append(summary)
            print(
                json.dumps(
                    {k: v for k, v in summary.items() if k not in ("samples", "mysql_snapshots")}
                )
            )
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
