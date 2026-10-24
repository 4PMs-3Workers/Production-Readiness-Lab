"""OTLP SDK diagnostics remain finite across every owned signal exporter."""

import asyncio
import logging
from contextlib import contextmanager
from typing import Protocol, cast

import pytest
import requests
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics.export import (
    Gauge,
    Metric,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from requests import PreparedRequest
from requests.adapters import HTTPAdapter
from requests.models import Response

from app.core.config import Settings
from app.observability.runtime import TelemetryRuntime
from app.observability.transport import (
    SAFE_OTLP_CONNECTION_ERROR,
    SAFE_OTLP_REQUEST_ERROR,
    SAFE_OTLP_RESPONSE_REASON,
    SanitizingOTLPSession,
)
from app.observability.worker import WorkerObserver

RESPONSE_CANARY = "fictional-otlp-reason-canary-35"
EXCEPTION_CANARY = "fictional-otlp-exception-canary-35"
ENDPOINT = "http://otel.invalid"


class _MetricExporterWithTimeout(Protocol):
    """Installed OTLP exporter accepts timeout_millis beyond its base SDK stub."""

    def export(
        self, metrics_data: MetricsData, timeout_millis: float | None = None
    ) -> MetricExportResult: ...


class StubResponseAdapter(HTTPAdapter):
    def __init__(self, *, status=400, reason=RESPONSE_CANARY, headers=None, error=None):
        super().__init__()
        self.status = status
        self.reason = reason
        self.headers = headers or {}
        self.error = error
        self.calls = 0
        self.payloads = []

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: object = True,
        cert: object = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        self.calls += 1
        self.payloads.append(request.body)
        if self.error is not None:
            raise self.error
        response = Response()
        response.status_code = self.status
        response.reason = self.reason
        response.headers.update(self.headers)
        response.url = request.url or ENDPOINT
        response.request = request
        response._content = b""
        return response


class TrackingSession(SanitizingOTLPSession):
    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self):
        self.close_count += 1
        super().close()


class MessageCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextmanager
def _capture_exporter_diagnostics(exporter):
    logger = logging.getLogger(type(exporter).__module__)
    handlers = tuple(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    collector = MessageCollector()
    logger.addHandler(collector)
    try:
        yield collector.messages
    finally:
        logger.removeHandler(collector)
        assert tuple(logger.handlers) == handlers
        assert logger.level == level
        assert logger.propagate == propagate


def _exporter(signal, session, *, timeout=0.05):
    if signal == "logs":
        return OTLPLogExporter(endpoint=f"{ENDPOINT}/v1/logs", timeout=timeout, session=session)
    if signal == "traces":
        return OTLPSpanExporter(endpoint=f"{ENDPOINT}/v1/traces", timeout=timeout, session=session)
    return OTLPMetricExporter(endpoint=f"{ENDPOINT}/v1/metrics", timeout=timeout, session=session)


def _export(exporter, signal):
    if signal == "metrics":
        return exporter.export(MetricsData(()), timeout_millis=50)
    return exporter.export([])


@pytest.mark.parametrize("signal", ["logs", "traces", "metrics"])
def test_sdk_exporter_reason_diagnostics_do_not_include_response_canary(signal):
    session = SanitizingOTLPSession()
    adapter = StubResponseAdapter()
    session.mount("http://otel.invalid", adapter)
    exporter = _exporter(signal, session)
    try:
        with _capture_exporter_diagnostics(exporter) as messages:
            _export(exporter, signal)
        assert adapter.calls == 1
        assert messages
        assert any(SAFE_OTLP_RESPONSE_REASON in message for message in messages)
        assert all(RESPONSE_CANARY not in message for message in messages)
    finally:
        exporter.shutdown()


@pytest.mark.parametrize("signal", ["logs", "traces", "metrics"])
@pytest.mark.parametrize(
    ("exception_type", "calls", "safe_message"),
    [
        (requests.exceptions.ReadTimeout, 2, None),
        (requests.exceptions.RequestException, 1, SAFE_OTLP_REQUEST_ERROR),
    ],
)
def test_sdk_exporter_exception_diagnostics_keep_retry_class_without_canary(
    signal, exception_type, calls, safe_message
):
    session = SanitizingOTLPSession()
    adapter = StubResponseAdapter(error=exception_type(EXCEPTION_CANARY))
    session.mount("http://otel.invalid", adapter)
    exporter = _exporter(signal, session)
    try:
        with _capture_exporter_diagnostics(exporter) as messages:
            _export(exporter, signal)
        assert adapter.calls == calls
        assert messages
        assert all(EXCEPTION_CANARY not in message for message in messages)
        if safe_message is not None:
            assert any(safe_message in message for message in messages)
    finally:
        exporter.shutdown()


@pytest.mark.parametrize(
    "exception_type",
    [
        requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.Timeout,
        requests.exceptions.SSLError,
        requests.exceptions.ProxyError,
    ],
)
def test_sanitizing_session_maps_all_sdk_retryable_transport_exceptions(exception_type):
    session = SanitizingOTLPSession()
    session.mount(
        "http://otel.invalid",
        StubResponseAdapter(error=exception_type(EXCEPTION_CANARY)),
    )
    try:
        with pytest.raises(requests.exceptions.ConnectionError) as caught:
            session.request("POST", f"{ENDPOINT}/traces")
        assert str(caught.value) == SAFE_OTLP_CONNECTION_ERROR
        assert EXCEPTION_CANARY not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        session.close()


def test_sanitizing_session_maps_other_exceptions_to_safe_nonretryable_error():
    session = SanitizingOTLPSession()
    session.mount("http://otel.invalid", StubResponseAdapter(error=RuntimeError(EXCEPTION_CANARY)))
    try:
        with pytest.raises(requests.exceptions.RequestException) as caught:
            session.request("POST", f"{ENDPOINT}/logs")
        assert not isinstance(caught.value, requests.exceptions.ConnectionError)
        assert str(caught.value) == SAFE_OTLP_REQUEST_ERROR
        assert EXCEPTION_CANARY not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        session.close()


def test_sanitizing_session_preserves_http_status_and_retry_after_headers():
    session = SanitizingOTLPSession()
    adapter = StubResponseAdapter(
        status=503,
        headers={"Retry-After": "0", "X-Collector-Status": "busy"},
    )
    session.mount("http://otel.invalid", adapter)
    try:
        response = session.request("POST", f"{ENDPOINT}/metrics")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "0"
        assert response.headers["X-Collector-Status"] == "busy"
        assert response.reason == SAFE_OTLP_RESPONSE_REASON
        assert RESPONSE_CANARY not in response.reason
    finally:
        session.close()


def test_sdk_uses_preserved_retryable_status_and_retry_after():
    session = SanitizingOTLPSession()
    adapter = StubResponseAdapter(status=503, headers={"Retry-After": "0"})
    session.mount("http://otel.invalid", adapter)
    exporter = _exporter("metrics", session)
    try:
        with _capture_exporter_diagnostics(exporter) as messages:
            _export(exporter, "metrics")
        assert adapter.calls == 6
        assert messages
        assert all(RESPONSE_CANARY not in message for message in messages)
    finally:
        exporter.shutdown()


def test_actual_worker_metric_export_emits_one_bounded_otlp_wire_request():
    session = SanitizingOTLPSession()
    adapter = StubResponseAdapter(status=200, reason="OK")
    session.mount("http://otel.invalid", adapter)
    exporter = _exporter("metrics", session)
    point = NumberDataPoint(
        attributes={"worker.slot": 2},
        start_time_unix_nano=1_800_000_000_000_000_000,
        time_unix_nano=1_800_000_000_000_000_100,
        value=3,
    )
    data = MetricsData(
        (
            ResourceMetrics(
                Resource(
                    {
                        "service.name": "worker",
                        "deployment.environment.name": "test",
                    }
                ),
                (
                    ScopeMetrics(
                        InstrumentationScope("test.worker", "1.0"),
                        (  # one latest-state point
                            Metric(
                                "prl_worker_successful_scans_total",
                                "Successful worker scans.",
                                "1",
                                Gauge((point,)),
                            ),
                        ),
                        "",
                    ),
                ),
                "",
            ),
        )
    )
    try:
        cast(_MetricExporterWithTimeout, exporter).export(data, timeout_millis=50)
        assert adapter.calls == 1
        assert len(adapter.payloads[0]) <= 8192
        assert adapter.payloads[0]
    finally:
        exporter.shutdown()


class _SuccessAdapter(HTTPAdapter):
    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: object = True,
        cert: object = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        response = Response()
        response.status_code = 200
        response.reason = "OK"
        response.url = request.url or ENDPOINT
        response.request = request
        response._content = b""
        return response


@pytest.mark.asyncio
async def test_each_default_signal_exporter_owns_and_closes_a_distinct_session(monkeypatch):
    import app.observability.runtime as runtime_module
    import app.observability.worker as worker_module

    settings = Settings.model_validate(
        {
            "DATABASE_URL": "mysql+asyncmy://user:password@localhost:3306/prl",
            "APPLICATION_ORIGIN": "http://localhost:3000",
            "COOKIE_SECRET": "cookie-secret",
            "CSRF_SECRET": "csrf-secret",
            "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
            "TELEMETRY_ENABLED": True,
            "TELEMETRY_OTLP_ENDPOINT": ENDPOINT,
        }
    )
    sessions = []

    def session_factory():
        session = TrackingSession()
        session.mount("http://otel.invalid", _SuccessAdapter())
        sessions.append(session)
        return session

    monkeypatch.setattr(runtime_module, "SanitizingOTLPSession", session_factory)
    monkeypatch.setattr(worker_module, "SanitizingOTLPSession", session_factory)
    telemetry = TelemetryRuntime(settings, "worker", stdout_enabled=False)
    observer = WorkerObserver(settings, telemetry, export_interval_seconds=60)
    try:
        await observer.close(asyncio.get_running_loop().time() + 1)
        await telemetry.close(asyncio.get_running_loop().time() + 1)
    finally:
        await observer.close(asyncio.get_running_loop().time() + 1)
        await telemetry.close(asyncio.get_running_loop().time() + 1)

    assert len(sessions) == 3
    assert len({id(session) for session in sessions}) == 3
    assert all(session.close_count == 1 for session in sessions)
