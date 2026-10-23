import asyncio
import importlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def _runtime_module():
    if importlib.util.find_spec("app.observability") is None:
        pytest.fail("bounded telemetry runtime is not implemented")
    if importlib.util.find_spec("app.observability.runtime") is None:
        pytest.fail("bounded telemetry runtime is not implemented")
    return importlib.import_module("app.observability.runtime")


def _exporting_module():
    if importlib.util.find_spec("app.observability") is None:
        pytest.fail("bounded telemetry export lane is not implemented")
    if importlib.util.find_spec("app.observability.exporting") is None:
        pytest.fail("bounded telemetry export lane is not implemented")
    return importlib.import_module("app.observability.exporting")


async def _wait_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


def test_export_lane_bounds_admission_and_drops_newest():
    exporting = _exporting_module()
    entered, release = threading.Event(), threading.Event()

    def blocked_export(batch):
        entered.set()
        release.wait()
        return True

    lane = exporting.ExportLane(
        "logs", blocked_export, capacity=2, max_age=1, max_batch=1, max_item_bytes=8192
    )
    try:
        assert lane.submit(b"active")
        assert entered.wait(1)
        assert lane.submit(b"queued")
        assert lane.submit(b"drop-newest") is False
        assert lane.accepted_count == 2
        assert lane.dropped["queue_full"] == 1
    finally:
        release.set()
        asyncio.run(lane.close(time.monotonic() + 1))


def test_export_lane_rejects_oversized_items_and_nonfinite_json():
    exporting = _exporting_module()
    lane = exporting.ExportLane(
        "logs", lambda batch: True, capacity=2, max_age=1, max_batch=1, max_item_bytes=8
    )
    assert lane.submit(b"123456789") is False
    assert lane.dropped["oversize"] == 1
    with pytest.raises(ValueError):
        exporting.encode_item({"duration_seconds": math.nan})
    with pytest.raises(ValueError):
        exporting.encode_item({"duration_seconds": math.inf})
    asyncio.run(lane.close(time.monotonic() + 1))


async def test_expired_export_items_are_discarded_without_replay():
    exporting = _exporting_module()
    entered, release = threading.Event(), threading.Event()
    exported = []

    def blocked_export(batch):
        entered.set()
        release.wait()
        exported.extend(batch)
        return True

    lane = exporting.ExportLane(
        "logs", blocked_export, capacity=2, max_age=0.03, max_batch=1, max_item_bytes=8192
    )
    try:
        assert lane.submit(b"active")
        assert entered.wait(1)
        assert lane.submit(b"expires")
        await asyncio.sleep(0.05)
        assert lane.dropped["expired"] == 2
    finally:
        release.set()
        await lane.close(asyncio.get_running_loop().time() + 1)
    assert exported == [b"active"]


async def test_export_failure_is_counted_once_without_exception_payloads():
    exporting = _exporting_module()

    def broken_export(batch):
        raise RuntimeError("private-sql-and-token-canary")

    lane = exporting.ExportLane(
        "logs", broken_export, capacity=2, max_age=1, max_batch=1, max_item_bytes=8192
    )
    assert lane.submit(b"safe")
    await _wait_until(lambda: lane.dropped["export_failed"] == 1)
    assert lane.dropped["export_failed"] == 1
    await lane.close(asyncio.get_running_loop().time() + 1)


async def test_close_respects_short_owner_deadline_and_counts_shutdown_discards():
    exporting = _exporting_module()
    entered, release = threading.Event(), threading.Event()

    def blocked_export(batch):
        entered.set()
        release.wait()
        return True

    lane = exporting.ExportLane(
        "traces", blocked_export, capacity=2, max_age=1, max_batch=1, max_item_bytes=8192
    )
    try:
        assert lane.submit(b"active")
        assert entered.wait(1)
        assert lane.submit(b"queued")
        started = asyncio.get_running_loop().time()
        await lane.close(started + 0.1)
        assert asyncio.get_running_loop().time() - started < 0.2
        assert lane.dropped["shutdown"] == 2
    finally:
        release.set()


async def test_runtime_close_caps_far_owner_deadline_at_one_second(auth_settings):
    runtime_module = _runtime_module()
    stdout_started, logs_started, traces_started = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    release = threading.Event()

    def blocked_sink(_record):
        stdout_started.set()
        release.wait()

    def blocked_export(started):
        def export(_batch):
            started.set()
            release.wait()
            return True

        return export

    runtime = runtime_module.TelemetryRuntime(
        auth_settings,
        "api",
        stdout_sink=blocked_sink,
        log_exporter=blocked_export(logs_started),
        span_exporter=blocked_export(traces_started),
    )
    try:
        assert runtime.emit({"event": "worker.heartbeat"})
        with runtime.span("http.server"):
            pass
        assert stdout_started.wait(1)
        assert logs_started.wait(1)
        assert traces_started.wait(1)

        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(runtime.close(asyncio.get_running_loop().time() + 30), timeout=1.2)
        assert asyncio.get_running_loop().time() - started < 1.1
    finally:
        release.set()
        await runtime.close(asyncio.get_running_loop().time() + 1)


async def test_runtime_redacts_unallowlisted_fields_and_isolates_providers(auth_settings):
    from opentelemetry import trace as global_trace

    runtime_module = _runtime_module()
    secret = "sql-token-cookie-consultation-canary"
    output_a, output_b = [], []
    runtime_a = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=output_a.append)
    runtime_b = runtime_module.TelemetryRuntime(
        auth_settings, "worker", stdout_sink=output_b.append
    )
    global_provider = global_trace.get_tracer_provider()
    try:
        assert runtime_a.trace_provider is not runtime_b.trace_provider
        assert runtime_a.metrics.registry is not runtime_b.metrics.registry
        assert global_trace.get_tracer_provider() is global_provider
        assert runtime_a.emit(
            {
                "event": "worker.heartbeat",
                "run_id": "run-safe-123",
                "private_sql": secret,
                "error_payload": secret,
            }
        )
        assert not runtime_a.emit({"event": "worker.heartbeat", "duration_seconds": math.nan})
    finally:
        await runtime_a.close(asyncio.get_running_loop().time() + 1)
        await runtime_b.close(asyncio.get_running_loop().time() + 1)
    all_output = "\n".join(output_a + output_b)
    assert secret not in all_output
    row = json.loads(output_a[0])
    assert row["service"] == "api" and row["event"] == "worker.heartbeat"
    assert "private_sql" not in row and "error_payload" not in row


async def test_span_budget_limits_nested_spans_per_request(auth_settings):
    runtime_module = _runtime_module()
    exported = []
    runtime = runtime_module.TelemetryRuntime(
        auth_settings.model_copy(update={"telemetry_max_spans_per_request": 2}),
        "api",
        stdout_sink=lambda _: None,
        span_exporter=lambda batch: (exported.extend(batch), True)[1],
    )
    try:
        with runtime.span("http.server"):
            with runtime.span("db.query"):
                pass
            with runtime.span("db.commit"):
                pass
        await runtime.close(asyncio.get_running_loop().time() + 1)
        assert [span.name for span in exported] == ["db.query", "http.server"]
    finally:
        await runtime.close(asyncio.get_running_loop().time() + 1)


async def test_runtime_admission_counts_the_active_stdout_write(auth_settings):
    runtime_module = _runtime_module()
    entered, release = threading.Event(), threading.Event()

    def blocked_sink(record):
        entered.set()
        release.wait()

    settings = auth_settings.model_copy(update={"telemetry_log_queue_capacity": 1})
    runtime = runtime_module.TelemetryRuntime(settings, "api", stdout_sink=blocked_sink)
    try:
        assert runtime.emit({"event": "worker.heartbeat"})
        assert entered.wait(1)
        assert not runtime.emit({"event": "worker.heartbeat"})
        assert runtime.accepted_count == 1
        assert runtime.dropped["queue_full"] == 1
    finally:
        release.set()
        await runtime.close(asyncio.get_running_loop().time() + 1)


async def test_runtime_counts_failed_stdout_export_once(auth_settings):
    runtime_module = _runtime_module()

    def broken_sink(record):
        raise RuntimeError("SQL password must stay private")

    runtime = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=broken_sink)
    try:
        assert runtime.emit({"event": "worker.heartbeat"})
        await runtime.close(asyncio.get_running_loop().time() + 1)
        rendered = runtime.metrics.render()
        assert 'prl_telemetry_failed_total{service="api",signal="stdout"} 1.0' in rendered
        assert (
            'prl_telemetry_dropped_total{reason="export_failed",service="api",signal="stdout"} 1.0'
            in rendered
        )
        assert "SQL password" not in rendered
    finally:
        await runtime.close(asyncio.get_running_loop().time() + 1)


async def test_runtime_uses_public_otlp_http_exporters_and_sanitizes_wire_payloads(
    auth_settings, monkeypatch
):
    runtime_module = _runtime_module()
    canary = "consultation-SQL-password-token-canary"
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"private.sql={canary}")
    received = []
    received_event = threading.Event()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            received.append((self.path, self.rfile.read(size)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            received_event.set()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    stdout = []
    settings = auth_settings.model_copy(
        update={
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": f"http://127.0.0.1:{server.server_port}",
        }
    )
    runtime = runtime_module.TelemetryRuntime(settings, "api", stdout_sink=stdout.append)
    runtime.register_routes(["/api/v1/appointments/{appointment_id}"])
    try:
        assert runtime.emit(
            {
                "event": "http.server.request",
                "method": "GET",
                "route": "/api/v1/appointments/{appointment_id}",
                "private_sql": canary,
                "exception": canary,
            }
        )
        from opentelemetry.trace import Status, StatusCode

        with runtime.span("http.server") as span:
            span.set_attribute("http.route", "/api/v1/appointments/{appointment_id}")
            span.set_attribute("db.statement", canary)
            span.set_attribute("exception.message", canary)
            span.add_event(canary, {"private_sql": canary})
            span.set_status(Status(StatusCode.ERROR, canary))
        with runtime.trace_provider.get_tracer(canary).start_as_current_span(
            "db.query"
        ) as raw_span:
            raw_span.set_attribute("http.route", "/api/v1/appointments/{appointment_id}")
            raw_span.add_event(canary, {"private_sql": canary})
        runtime.log_provider.get_logger(canary).emit(
            body=canary,
            event_name="worker.heartbeat",
            attributes={"private_sql": canary},
        )
        await runtime.close(asyncio.get_running_loop().time() + 1)
        assert received_event.wait(1)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
    payloads = b"".join(body for _, body in received)
    assert {path for path, _ in received} == {"/v1/logs", "/v1/traces"}
    assert canary.encode() not in payloads
    assert canary not in "\n".join(stdout)


async def test_otlp_http_failure_response_body_never_reaches_diagnostics(auth_settings, caplog):
    import logging

    runtime_module = _runtime_module()
    canary = "receiver-body-password-token-canary"

    class RejectingReceiver(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(size)
            body = canary.encode()
            self.send_response(503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RejectingReceiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = auth_settings.model_copy(
        update={
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": f"http://127.0.0.1:{server.server_port}",
        }
    )
    stdout = []
    runtime = runtime_module.TelemetryRuntime(settings, "api", stdout_sink=stdout.append)
    caplog.set_level(logging.DEBUG)
    try:
        assert runtime.emit({"event": "http.server.request", "method": "GET", "status": 503})
        with runtime.span("http.server"):
            pass
        await runtime.close(asyncio.get_running_loop().time() + 1)
        await asyncio.sleep(0.02)
    finally:
        server.shutdown()
        server.server_close()
    assert canary not in caplog.text
    assert canary not in "\n".join(stdout)


async def test_runtime_metrics_keep_api_registry_per_instance(auth_settings):
    from prometheus_client import REGISTRY

    runtime_module = _runtime_module()
    global_metric_names_before = {metric.name for metric in REGISTRY.collect()}
    first = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=lambda _: None)
    second = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=lambda _: None)
    try:
        assert first.metrics.registry is not second.metrics.registry
        first.metrics.record_http_request("/not-registered", "GET", 200, 0.1)
        first_render = first.metrics.render()
        second_render = second.metrics.render()
        sample = 'prl_http_requests_total{method="GET",route="__unmatched__",status="200"} 1.0'
        assert sample in first_render
        assert sample not in second_render
        global_metric_names_after = {metric.name for metric in REGISTRY.collect()}
        assert "prl_http_requests" not in global_metric_names_after
        assert global_metric_names_before == global_metric_names_after
    finally:
        await first.close(asyncio.get_running_loop().time() + 1)
        await second.close(asyncio.get_running_loop().time() + 1)


async def test_runtime_does_not_install_global_logging_handlers(auth_settings):
    import logging

    runtime_module = _runtime_module()
    root_handlers = tuple(logging.getLogger().handlers)
    first = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=lambda _: None)
    try:
        assert tuple(logging.getLogger().handlers) == root_handlers
    finally:
        await first.close(asyncio.get_running_loop().time() + 1)


async def test_sanitizers_bound_route_registration_and_malformed_span_attributes():
    _runtime_module()
    records = importlib.import_module("app.observability.records")
    routes = records.RouteAllowlist()
    routes.register(f"/api/v1/r{index}" for index in range(128))
    assert len(routes.routes) == 129
    with pytest.raises(ValueError):
        records.RouteAllowlist().register(f"/api/v1/r{index}" for index in range(1000000))
    attributes = {
        "db.operation.name": [],
        "http.route": "/api/v1/r1",
        **{f"unknown-{index}": "ignored" for index in range(1000)},
    }
    safe = records.sanitize_span_attributes(attributes, routes=routes, limit=16)
    assert safe == {"http.route": "/api/v1/r1"}
    assert records.sanitize_span_attributes(
        {"http.route": "/api/v1/r1"}, routes=routes, max_attribute_chars=9
    ) == {"http.route": records.UNMATCHED_ROUTE}
    metrics = importlib.import_module("app.observability.metrics").TelemetryMetrics(routes)
    metrics.record_http_request([], [], [], object())
    metrics.record_db_statement([], object())
    assert 'status="500"' not in metrics.render()


async def test_route_inventory_is_bounded_and_same_set_registration_is_idempotent(auth_settings):
    runtime_module = _runtime_module()
    runtime = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=lambda _: None)
    try:
        templates = ["/api/v1/test/{record_id}"]
        runtime.register_routes(templates)
        runtime.register_routes(tuple(templates))
        with pytest.raises(ValueError):
            runtime.register_routes(["/api/v1/other"])
        with pytest.raises(ValueError):
            runtime.register_routes(["/api/v1/test?token=value"])
        with pytest.raises(ValueError):
            runtime.register_routes(["/" + "a" * 128])
    finally:
        await runtime.close(asyncio.get_running_loop().time() + 1)


async def test_route_inventory_must_be_ready_before_http_observation(auth_settings):
    runtime_module = _runtime_module()
    runtime = runtime_module.TelemetryRuntime(auth_settings, "api", stdout_sink=lambda _: None)
    try:
        runtime.metrics.record_http_request("/api/v1/late", "GET", 200, 0.01)
        with pytest.raises(ValueError):
            runtime.register_routes(["/api/v1/late"])
    finally:
        await runtime.close(asyncio.get_running_loop().time() + 1)


def test_process_exits_while_the_otlp_http_export_is_blocked():
    script = r"""
import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, 'src/backend')
from app.core.config import Settings
from app.observability.runtime import TelemetryRuntime
entered = threading.Event()
release = threading.Event()
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        entered.set()
        release.wait(10)
    def log_message(self, format, *args):
        return
server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
threading.Thread(target=server.serve_forever, daemon=True).start()
settings = Settings.model_validate({
    'DATABASE_URL': 'mysql+asyncmy://user:password@localhost:3306/prl',
    'APPLICATION_ORIGIN': 'http://localhost:3000',
    'COOKIE_SECRET': 'cookie-secret',
    'CSRF_SECRET': 'csrf-secret',
    'ACCEPTED_SCHEMA_REVISIONS': 'a1b2c3d4e5f6',
    'TELEMETRY_ENABLED': True,
    'TELEMETRY_OTLP_ENDPOINT': f'http://127.0.0.1:{server.server_port}',
})
runtime = TelemetryRuntime(settings, 'api', stdout_sink=lambda record: None)
runtime.emit({'event': 'worker.heartbeat'})
async def main():
    if not await asyncio.to_thread(entered.wait, 1):
        raise RuntimeError('OTLP request did not reach receiver')
    started = asyncio.get_running_loop().time()
    await runtime.close(started + 0.05)
    assert asyncio.get_running_loop().time() - started < 0.2
asyncio.run(main())
"""
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert time.monotonic() - started < 2


def test_process_exits_with_a_blocked_stdout_writer():
    script = r"""
import asyncio
import os
import sys
import threading
sys.path.insert(0, 'src/backend')
from app.core.publishing import Publisher
blocked = threading.Event()
entered = threading.Event()
def block_write(descriptor, payload):
    entered.set()
    blocked.wait()
    return len(payload)
os.write = block_write
publisher = Publisher(capacity=1, timeout=1)
publisher.submit('safe-record')
async def main():
    if not await asyncio.to_thread(entered.wait, 1):
        raise RuntimeError('writer did not enter')
    await publisher.close(asyncio.get_running_loop().time() + 0.05)
asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
