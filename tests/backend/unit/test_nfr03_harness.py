import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import yaml
from tests.backend.integration import nfr03_native as native
from tests.backend.integration import test_appointment_export_nfr03 as probe


def fake_workload(monkeypatch, *, stalled=False, latency=0.3):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, *args, **kwargs):
            await asyncio.sleep(latency)
            return SimpleNamespace(status_code=200)

        async def aclose(self):
            pass

    calls = []

    async def snapshot(*args):
        calls.append(1)
        if stalled and len(calls) > 1:
            await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(probe.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        probe,
        "rows",
        lambda response: [{"id": str(UUID(int=100 + i))} for i in reversed(range(100))],
    )
    monkeypatch.setattr(probe, "mysql_snapshot", snapshot)
    monkeypatch.setattr(probe, "PHASES", ("first_burst",))
    monkeypatch.setattr(probe, "WATCHER_STOP_SECONDS", 0.03, raising=False)
    app = SimpleNamespace(
        state=SimpleNamespace(engine=SimpleNamespace(pool=SimpleNamespace(checkedout=lambda: 3)))
    )
    args = (
        app,
        SimpleNamespace(scalar=AsyncMock(return_value="8.4")),
        {"bursts": []},
        None,
        "production",
        set(),
        SimpleNamespace(client=SimpleNamespace(cookies={})),
        SimpleNamespace(url=SimpleNamespace(database="fixture")),
    )
    return args


async def test_fast_burst_collects_multiple_lag_samples(monkeypatch):
    args = fake_workload(monkeypatch)
    await probe.run_bursts(*args)
    burst = args[2]["bursts"][0]
    assert burst["errors"] == 0
    assert burst["event_loop_lag_samples"] >= 2


async def test_asgi_snapshot_has_pool_occupancy(monkeypatch):
    args = fake_workload(monkeypatch)
    await probe.run_bursts(*args)
    assert any(s.get("pool_checked_out") == 3 for s in args[2]["bursts"][0]["mysql_snapshots"])


async def test_observer_deadline_is_inside_snapshot(monkeypatch):
    monkeypatch.setattr(probe, "OBSERVER_SECONDS", 0.02, raising=False)
    connection = SimpleNamespace(execute=AsyncMock(side_effect=lambda *args: None))

    async def blocked(*args):
        await asyncio.Event().wait()

    connection.execute = blocked
    task = asyncio.create_task(probe.mysql_snapshot(connection, "fixture"))
    try:
        done, _ = await asyncio.wait({task}, timeout=0.3)
        assert task in done
        with pytest.raises(TimeoutError):
            task.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_stalled_watcher_preserves_completed_responses(monkeypatch):
    args = fake_workload(monkeypatch, stalled=True)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(probe.run_bursts(*args), timeout=1.0)
    assert len(args[2]["bursts"][0]["samples"]) == 80


@pytest.mark.parametrize("diagnostic", ["0", "1"])
async def test_native_worker_count_overrides_parent(monkeypatch, auth_settings, diagnostic):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("NFR03_SERVER_DIAGNOSTICS", diagnostic)
    process = SimpleNamespace(
        poll=lambda: None, terminate=MagicMock(), wait=MagicMock(), kill=MagicMock()
    )
    launch = MagicMock(return_value=process)
    monkeypatch.setattr(native.subprocess, "Popen", launch)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(native.httpx, "AsyncClient", Client)
    async with native.native_api(auth_settings):
        argv = launch.call_args.args[0]
        assert argv[argv.index("--workers") + 1] == "1"
    process.terminate.assert_called_once()


def test_workflow_tracks_direct_inputs():
    root = Path(__file__).resolve().parents[3]
    config = yaml.load(
        (root / ".github/workflows/nfr03-export-lab.yml").read_text(), Loader=yaml.BaseLoader
    )
    paths = config["on"]["pull_request"]["paths"]
    assert {
        "requirements*.txt",
        "pyproject.toml",
        "tests/backend/conftest.py",
        "tests/backend/integration/**",
        "alembic.ini",
        "tools/infra/offhost/**",
    } <= set(paths)


async def test_short_burst_does_not_charge_observation_tail_to_requests(monkeypatch):
    args = fake_workload(monkeypatch, latency=0.001)
    await probe.run_bursts(*args)
    burst = args[2]["bursts"][0]
    assert burst["event_loop_lag_samples"] >= 2
    assert burst["lag_observation_window_seconds"] >= burst["request_window_seconds"]
    assert max(s["latency_seconds"] for s in burst["samples"]) <= burst["request_window_seconds"]


async def test_native_never_samples_the_parent_pool(monkeypatch):
    args = fake_workload(monkeypatch)
    await probe.run_bursts(*args, target="http://127.0.0.1:12345")
    assert all("pool_checked_out" not in s for s in args[2]["bursts"][0]["mysql_snapshots"])


@pytest.mark.parametrize("samples,p95,saturated", [(0, None, False), (2, 0.2, True)])
async def test_missing_evidence_is_not_measured_saturation(
    monkeypatch, samples, p95, saturated, tmp_path
):
    report = {
        "source_sha": "0" * 40,
        "bursts": [
            {
                "phase": "first_burst",
                "samples": [],
                "event_loop_lag_samples": samples,
                "event_loop_lag_p95_seconds": p95,
                "lag_missed_tick_fraction": 0.0,
            }
        ],
    }
    monkeypatch.setattr(probe, "save_report", lambda *args: tmp_path / "report.json")
    await probe.finish_report(SimpleNamespace(dispose=AsyncMock()), report, True)
    assert report["generator_saturated"] is saturated
    assert report["lag_evidence_complete"] is (samples >= 2)
    assert not report["passed"]


async def test_dispose_timeout_still_writes_report(monkeypatch, tmp_path):
    async def blocked():
        await asyncio.Event().wait()

    monkeypatch.setattr(probe, "CLEANUP_SECONDS", 0.02)
    saved = MagicMock(return_value=tmp_path / "report.json")
    monkeypatch.setattr(probe, "save_report", saved)
    report = {"source_sha": "0" * 40, "bursts": []}
    await asyncio.wait_for(probe.finish_report(SimpleNamespace(dispose=blocked), report, True), 0.5)
    assert not report["completed"] and not report["passed"]
    saved.assert_called_once()


def test_observer_options_are_supported_by_pinned_driver():
    import inspect

    import asyncmy

    inspect.signature(asyncmy.connect).bind_partial(**probe.OBSERVER_CONNECT_ARGS)
