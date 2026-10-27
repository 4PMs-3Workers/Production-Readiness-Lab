"""Worker metrics must reflect durable events without inventing freshness."""

import asyncio
import importlib.util
import inspect
import threading
from uuid import uuid4

from app.core.config import Settings
from app.observability.runtime import TelemetryRuntime, create_telemetry
from app.worker.models import WorkerState
from app.worker.reporting import Reporter

VALID_SETTINGS = {
    "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "cookie-secret",
    "CSRF_SECRET": "csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
}


def test_worker_replica_slot_defaults_to_one_and_is_bounded():
    settings = Settings.model_validate(VALID_SETTINGS)
    assert getattr(settings, "worker_replica_slot", None) == 1

    for slot in (0, 9):
        try:
            Settings.model_validate({**VALID_SETTINGS, "WORKER_REPLICA_SLOT": slot})
        except ValueError:
            continue
        raise AssertionError("worker replica slot must be restricted to 1..8")


def test_reporter_has_an_independent_observer_hook():
    assert "observer" in inspect.signature(Reporter).parameters


def test_worker_runtime_can_disable_its_stdout_publisher():
    assert "stdout_enabled" in inspect.signature(TelemetryRuntime).parameters
    assert "stdout_enabled" in inspect.signature(create_telemetry).parameters


def test_worker_observer_is_a_public_worker_observability_component():
    assert importlib.util.find_spec("app.observability.worker") is not None
    assert importlib.util.find_spec("app.observability.worker_metrics") is not None
    assert importlib.util.find_spec("app.observability.metric_exporting") is not None


async def test_disabled_stdout_keeps_otlp_log_admission_independent(auth_settings):
    exported = []
    runtime = TelemetryRuntime(
        auth_settings,
        "worker",
        stdout_enabled=False,
        log_exporter=lambda records: (exported.extend(records), True)[1],
    )
    try:
        assert not runtime.emit({"event": "worker.heartbeat", "phase": "running"})
        assert runtime.accepted_count == 0
        assert runtime._publisher is None
        await runtime.close(asyncio.get_running_loop().time() + 1)
    finally:
        await runtime.close(asyncio.get_running_loop().time() + 1)

    assert len(exported) == 1
    assert exported[0].log_record.event_name == "worker.heartbeat"
    assert 'signal="stdout"' not in runtime.metrics.render()


async def test_reporter_observes_each_event_when_stdout_admission_is_full():
    entered, release = threading.Event(), threading.Event()
    output = []
    events = []

    class Observer:
        def observe(self, event, state, *, reason=None, result=None):
            events.append((event, state.worker_id))

    def blocked_sink(record):
        entered.set()
        release.wait()
        output.append(record)

    reporter = Reporter(blocked_sink, observer=Observer())
    state = WorkerState(uuid4())
    accepted = []
    try:
        accepted.append(reporter.emit("starting", state))
        assert entered.wait(1)
        accepted.extend(reporter.emit("heartbeat", state) for _ in range(300))
    finally:
        release.set()
    await reporter.close(asyncio.get_running_loop().time() + 1)

    assert len(events) == 301
    assert all(worker_id == state.worker_id for _, worker_id in events)
    assert any(not admitted for admitted in accepted)
    assert len(output) < len(events)


async def test_observer_failure_does_not_change_stdout_admission():
    output = []

    class BrokenObserver:
        def observe(self, *args, **kwargs):
            raise RuntimeError("private telemetry error")

    reporter = Reporter(output.append, observer=BrokenObserver())
    assert reporter.emit("heartbeat", WorkerState(uuid4()))
    await reporter.close(asyncio.get_running_loop().time() + 1)
    assert len(output) == 1
