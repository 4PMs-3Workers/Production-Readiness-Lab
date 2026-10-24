"""Worker metric snapshots reflect real observations and bounded ownership."""

import asyncio
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.sdk.metrics.export import (
    Gauge,
    Histogram,
    HistogramDataPoint,
    Metric,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope

from app.core.config import Settings
from app.observability.runtime import TelemetryRuntime
from app.observability.worker import WorkerObserver
from app.worker.models import CandidateResult, FailureReason, Outcome, WorkerState

VALID_SETTINGS = {
    "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
    "APPLICATION_ORIGIN": "http://localhost:3000",
    "COOKIE_SECRET": "cookie-secret",
    "CSRF_SECRET": "csrf-secret",
    "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
}


class RecordingExporter:
    def __init__(self):
        self.batches: list[MetricsData] = []
        self.entered = threading.Event()
        self.second_entered = threading.Event()
        self.release = threading.Event()
        self.second_release = threading.Event()
        self.block = False
        self.call_count = 0

    def export(self, data: MetricsData, timeout_millis: float | None = None):
        self.batches.append(data)
        self.call_count += 1
        self.entered.set()
        if self.call_count >= 2:
            self.second_entered.set()
        if self.block:
            (self.second_release if self.call_count >= 2 else self.release).wait()
        return MetricExportResult.SUCCESS

    def shutdown(self):
        return None


def _settings(slot=1):
    return Settings.model_validate({**VALID_SETTINGS, "WORKER_REPLICA_SLOT": slot})


def _points(
    exporter: RecordingExporter,
) -> Iterator[tuple[str, NumberDataPoint | HistogramDataPoint]]:
    for batch in exporter.batches:
        for resource_metrics in batch.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if isinstance(metric.data, (Gauge, Sum)):
                        for point in metric.data.data_points:
                            yield metric.name, point
                    elif isinstance(metric.data, Histogram):
                        for point in metric.data.data_points:
                            yield metric.name, point


def _latest_number_point(
    exporter: RecordingExporter, name: str, **attributes: str | int
) -> NumberDataPoint:
    values = [
        point
        for metric_name, point in _points(exporter)
        if isinstance(point, NumberDataPoint)
        if metric_name == name
        and point.attributes is not None
        and all(point.attributes.get(key) == value for key, value in attributes.items())
    ]
    if not values:
        raise AssertionError(f"no numeric point found for {name}")
    return max(values, key=lambda point: point.time_unix_nano)


def _latest_histogram_point(exporter: RecordingExporter, name: str) -> HistogramDataPoint:
    values = [
        point
        for metric_name, point in _points(exporter)
        if isinstance(point, HistogramDataPoint) and metric_name == name
    ]
    if not values:
        raise AssertionError(f"no histogram point found for {name}")
    return max(values, key=lambda point: point.time_unix_nano)


def _snapshot(value: str) -> MetricsData:
    point = NumberDataPoint(
        attributes={"test.latest": value},
        start_time_unix_nano=1,
        time_unix_nano=1,
        value=1,
    )
    metric = Metric("test.latest", "Latest value marker.", "1", Gauge((point,)))
    scope = ScopeMetrics(InstrumentationScope("test.worker", "1.0"), (metric,), "")
    resource_metrics = ResourceMetrics(Resource({"service.name": "test"}), (scope,), "")
    return MetricsData((resource_metrics,))


def _snapshot_value(data: MetricsData) -> str:
    metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
    assert isinstance(metric.data, Gauge)
    attributes = metric.data.data_points[0].attributes
    assert attributes is not None
    return str(attributes["test.latest"])


async def _wait_for_snapshots(exporter, count, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    times: set[int] = set()
    while loop.time() < deadline:
        times = {
            point.time_unix_nano
            for name, point in _points(exporter)
            if name == "prl_worker_started_timestamp_seconds"
        }
        if len(times) >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"only {len(times)} snapshots were exported")


async def _wait_for_event(event, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if event.is_set():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("event was not set before timeout")


@pytest.mark.asyncio
async def test_repeated_events_failed_scans_and_export_ticks_keep_durable_state():
    settings = _settings(slot=3)
    telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    exporter = RecordingExporter()
    base = datetime(2026, 10, 24, tzinfo=UTC)
    clock_value = 0

    def clock():
        nonlocal clock_value
        value = base + timedelta(seconds=clock_value)
        clock_value += 1
        return value

    observer = WorkerObserver(
        settings,
        telemetry,
        exporter=exporter,
        export_interval_seconds=0.02,
        clock=clock,
    )
    state = WorkerState(uuid4())
    observer.observe("starting", state)

    scan_time = base + timedelta(seconds=1)
    state.phase = "running"
    state.database_ok = True
    state.successful_scans = 1
    state.last_successful_scan = scan_time
    state.selected = 20
    state.backlog_count = 20
    state.backlog_capped = True
    state.oldest_overdue_seconds = 90.0
    observer.observe("ready", state)

    result = CandidateResult(
        uuid4(), "worker-private-request", Outcome.FAILED, 1, 0.25, FailureReason.COMMIT_UNKNOWN
    )
    state.failed = 1
    state.database_ok = False
    observer.observe("candidate", state, result=result)
    observer.observe("candidate", state, result=result)

    observer.observe("heartbeat", state)
    await _wait_for_snapshots(exporter, 2)
    heartbeat_time = base + timedelta(seconds=4)

    state.failed_scans = 1
    state.selected = 0
    state.last_scan_duration_seconds = 1.5
    observer.observe("scan", state, reason=FailureReason.DATABASE_UNAVAILABLE)
    await observer.close(asyncio.get_running_loop().time() + 1)

    heartbeat_points = [
        point.value
        for name, point in _points(exporter)
        if name == "prl_worker_heartbeat_timestamp_seconds" and isinstance(point, NumberDataPoint)
    ]
    assert heartbeat_points
    assert set(heartbeat_points) == {heartbeat_time.timestamp()}
    assert _latest_number_point(exporter, "prl_worker_successful_scans_total").value == 1
    assert _latest_number_point(exporter, "prl_worker_failed_scans_total").value == 1
    assert _latest_number_point(exporter, "prl_worker_backlog_count").value == 20
    assert _latest_number_point(exporter, "prl_worker_backlog_capped").value == 1
    assert (
        _latest_number_point(exporter, "prl_worker_last_successful_scan_timestamp_seconds").value
        == scan_time.timestamp()
    )
    assert (
        _latest_number_point(exporter, "prl_worker_last_progress_timestamp_seconds").value
        == scan_time.timestamp()
    )
    assert _latest_histogram_point(exporter, "prl_worker_candidate_duration_seconds").count == 1
    assert (
        _latest_number_point(
            exporter,
            "prl_worker_candidate_failures_by_reason_total",
            reason="COMMIT_UNKNOWN",
        ).value
        == 1
    )

    for batch in exporter.batches:
        for resource_metrics in batch.resource_metrics:
            assert set(resource_metrics.resource.attributes) == {
                "service.name",
                "deployment.environment.name",
            }
            for scope_metrics in resource_metrics.scope_metrics:
                assert len(scope_metrics.metrics) <= 32
                for metric in scope_metrics.metrics:
                    assert len(metric.data.data_points) == 1
                    point_scope = ScopeMetrics(
                        scope_metrics.scope, (metric,), scope_metrics.schema_url
                    )
                    point_resource = ResourceMetrics(
                        resource_metrics.resource,
                        (point_scope,),
                        resource_metrics.schema_url,
                    )
                    point_request = encode_metrics(MetricsData((point_resource,)))
                    assert point_request.ByteSize() <= 8192
                    for point in metric.data.data_points:
                        assert point.attributes is not None
                        assert point.attributes.get("worker.slot") == 3
                        assert "worker-private-request" not in str(point.attributes)

    assert sum(encode_metrics(batch).ByteSize() for batch in exporter.batches) <= 65536

    await telemetry.close(asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_empty_and_unknown_backlog_values_stay_distinct():
    settings = _settings()
    telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    exporter = RecordingExporter()
    observer = WorkerObserver(
        settings,
        telemetry,
        exporter=exporter,
        export_interval_seconds=0.02,
        clock=lambda: datetime(2026, 10, 24, tzinfo=UTC),
    )
    state = WorkerState(uuid4())
    state.phase = "running"
    state.database_ok = True
    state.successful_scans = 1
    state.last_successful_scan = datetime(2026, 10, 24, tzinfo=UTC)
    state.backlog_count = 0
    state.backlog_capped = False
    observer.observe("ready", state)
    await _wait_for_snapshots(exporter, 1)

    state.successful_scans = 2
    state.last_successful_scan += timedelta(seconds=5)
    state.backlog_count = None
    state.backlog_capped = False
    state.oldest_overdue_seconds = None
    observer.observe("scan", state)
    await observer.close(asyncio.get_running_loop().time() + 1)

    successful_scan_point = _latest_number_point(
        exporter, "prl_worker_last_successful_scan_timestamp_seconds"
    )
    backlog_points = [
        point
        for name, point in _points(exporter)
        if name == "prl_worker_backlog_count" and isinstance(point, NumberDataPoint)
    ]
    capped_points = [
        point
        for name, point in _points(exporter)
        if name == "prl_worker_backlog_capped" and isinstance(point, NumberDataPoint)
    ]
    assert any(point.value == 0 for point in backlog_points)
    assert (
        backlog_points
        and max(point.time_unix_nano for point in backlog_points)
        < successful_scan_point.time_unix_nano
    )
    assert (
        capped_points
        and max(point.time_unix_nano for point in capped_points)
        < successful_scan_point.time_unix_nano
    )
    assert not any(name == "prl_worker_oldest_overdue_seconds" for name, _ in _points(exporter))
    assert successful_scan_point.value == state.last_successful_scan.timestamp()
    await telemetry.close(asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_counter_reset_is_rejected_within_owner_and_restarts_for_new_owner():
    settings = _settings(slot=5)
    telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    exporter = RecordingExporter()
    observer = WorkerObserver(settings, telemetry, exporter=exporter, export_interval_seconds=60)
    state = WorkerState(uuid4())
    state.phase = "running"
    state.successful_scans = 3
    state.last_successful_scan = datetime(2026, 10, 24, tzinfo=UTC)
    observer.observe("ready", state)
    state.successful_scans = 0
    state.last_successful_scan = None
    observer.observe("heartbeat", state)
    await observer.close(asyncio.get_running_loop().time() + 1)

    assert _latest_number_point(exporter, "prl_worker_successful_scans_total").value == 3
    first_started = _latest_number_point(exporter, "prl_worker_started_timestamp_seconds").value
    await telemetry.close(asyncio.get_running_loop().time() + 1)

    restarted_telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    restarted_exporter = RecordingExporter()
    restarted = WorkerObserver(
        settings,
        restarted_telemetry,
        exporter=restarted_exporter,
        export_interval_seconds=60,
    )
    restarted.observe("starting", WorkerState(uuid4()))
    await restarted.close(asyncio.get_running_loop().time() + 1)

    assert _latest_number_point(restarted_exporter, "prl_worker_successful_scans_total").value == 0
    assert (
        _latest_number_point(restarted_exporter, "prl_worker_started_timestamp_seconds").value
        >= first_started
    )
    await restarted_telemetry.close(asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_latest_lane_coalesces_observations_while_one_export_is_blocked():
    from app.observability.metric_exporting import LatestMetricExportLane

    exporter = RecordingExporter()
    exporter.block = True
    latest = {"value": "first"}
    lane_ref = {}
    callback_lock_checks = []

    def on_queue_change(_size):
        lane = lane_ref.get("lane")
        if lane is None:
            return
        acquired = threading.Event()

        def read_lane_state():
            _ = lane.dropped
            acquired.set()

        threading.Thread(target=read_lane_state, daemon=True).start()
        callback_lock_checks.append(acquired.wait(0.2))

    lane = LatestMetricExportLane(
        lambda: (_snapshot(latest["value"]),),
        exporter,
        interval_seconds=0.02,
        max_age_seconds=1,
        network_timeout_seconds=0.5,
        on_queue_change=on_queue_change,
    )
    lane_ref["lane"] = lane
    try:
        lane.note_snapshot()
        await _wait_for_event(exporter.entered)
        for value in range(1000):
            latest["value"] = f"intermediate-{value}"
            lane.note_snapshot()
        latest["value"] = "latest"
        lane.note_snapshot()
        exporter.release.set()
        await _wait_for_event(exporter.second_entered)
        lane.request_close()
        exporter.second_release.set()
        await lane.close(asyncio.get_running_loop().time() + 1)
    finally:
        exporter.release.set()
        exporter.second_release.set()
        await lane.close(asyncio.get_running_loop().time() + 1)

    assert _snapshot_value(exporter.batches[0]) == "first"
    assert _snapshot_value(exporter.batches[1]) == "latest"
    assert len(exporter.batches) == 2
    assert callback_lock_checks and all(callback_lock_checks)


@pytest.mark.asyncio
async def test_latest_lane_close_returns_at_the_shared_short_deadline():
    from app.observability.metric_exporting import LatestMetricExportLane

    exporter = RecordingExporter()
    exporter.block = True
    lane = LatestMetricExportLane(
        lambda: (_snapshot("current"),),
        exporter,
        interval_seconds=0.01,
        max_age_seconds=1,
        network_timeout_seconds=0.5,
    )
    try:
        lane.note_snapshot()
        await _wait_for_event(exporter.entered)
        close_started = asyncio.get_running_loop().time()
        closed = await lane.close(asyncio.get_running_loop().time() + 0.03)
        assert not closed
        assert asyncio.get_running_loop().time() - close_started < 0.2
        assert lane.is_alive
    finally:
        exporter.release.set()
        await lane.close(asyncio.get_running_loop().time() + 1)


def test_invalid_observation_accounts_loss_after_releasing_state_lock(monkeypatch):
    from app.core.config import Settings

    settings = Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "cookie-secret",
            "CSRF_SECRET": "csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
        }
    )
    telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    observer = WorkerObserver(settings, telemetry)
    state = WorkerState(uuid4())
    state.successful_scans = 1
    state.last_successful_scan = datetime(2026, 10, 24, tzinfo=UTC)
    observer.observe("ready", state)

    state.successful_scans = 0
    lock_was_available = []
    original_record_drop = telemetry.metrics.record_drop

    def record_drop(*args, **kwargs):
        acquired = threading.Event()

        def acquire_observer_state():
            with observer._lock:
                acquired.set()

        contender = threading.Thread(target=acquire_observer_state, daemon=True)
        contender.start()
        lock_was_available.append(acquired.wait(0.2))
        contender.join(0.2)
        original_record_drop(*args, **kwargs)

    monkeypatch.setattr(telemetry.metrics, "record_drop", record_drop)
    observer.observe("heartbeat", state)

    assert lock_was_available == [True]
