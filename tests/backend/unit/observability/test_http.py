"""Correlated HTTP observations stay safe and bounded at the ASGI boundary."""

import json
import re
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

from app.core.config import Settings
from app.factory import ResponseInterruptedError, create_app
from app.observability.runtime import Service, TelemetryRuntime


@asynccontextmanager
async def app_client(
    app,
    *,
    client_address: tuple[str, int] = ("127.0.0.1", 12000),
    follow_redirects: bool = True,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(
            app=app,
            client=client_address,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:3000",
            follow_redirects=follow_redirects,
        ) as client:
            yield client


def settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "mysql+asyncmy://user:password@127.0.0.1:1/prl",
        "APPLICATION_ORIGIN": "http://localhost:3000",
        "COOKIE_SECRET": "cookie-secret",
        "CSRF_SECRET": "csrf-secret",
        "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
        "TRUSTED_GATEWAY_CIDRS": "127.0.0.0/8",
    }
    values.update(updates)
    return Settings.model_validate(values)


class JsonSink:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self.changed = threading.Event()

    def __call__(self, line: str) -> None:
        self.records.append(json.loads(line))
        self.changed.set()

    def wait_for(self, count: int) -> list[dict[str, object]]:
        deadline = monotonic() + 2
        while len(self.records) < count and monotonic() < deadline:
            self.changed.wait(deadline - monotonic())
            self.changed.clear()
        assert len(self.records) >= count
        return self.records


def instrumented_app(*, config: Settings | None = None):
    sink = JsonSink()
    runtimes: list[TelemetryRuntime] = []

    def telemetry_factory(current: Settings, *, service: Service) -> TelemetryRuntime:
        runtime = TelemetryRuntime(current, service, stdout_sink=sink)
        runtimes.append(runtime)
        return runtime

    app = create_app(config or settings(), telemetry_factory=telemetry_factory)

    @app.get("/__test/items/{item_id}", include_in_schema=False)
    async def item(item_id: str):
        return {"item_id": item_id}

    @app.get("/__test/crash", include_in_schema=False)
    async def crash():
        raise RuntimeError("SQL SELECT secret_error_payload=do-not-export")

    @app.get("/__test/stream", include_in_schema=False)
    async def interrupted_stream():
        async def chunks():
            yield b"partial"
            raise RuntimeError("stream_secret_payload=do-not-export")

        return StreamingResponse(chunks())

    @app.get("/__test/unclassified-429", include_in_schema=False)
    async def unclassified_429():
        raise HTTPException(
            429,
            detail="secret detail",
            headers={"X-PRL-Outcome": "login_throttled"},
        )

    @app.get("/__test/current-trace", include_in_schema=False)
    async def current_trace(request: Request):
        span = trace.get_current_span()
        context = span.get_span_context()
        parent = getattr(span, "parent", None)
        return {
            "trace_id": f"{context.trace_id:032x}",
            "parent_trace_id": None if parent is None else f"{parent.trace_id:032x}",
            "remote_context_absent": "traceparent" not in request.headers
            and "tracestate" not in request.headers
            and "baggage" not in request.headers,
        }

    return app, sink, runtimes


def _records_for(sink: JsonSink, request_id: str) -> list[dict[str, object]]:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        matching = [record for record in sink.records if record.get("request_id") == request_id]
        if matching:
            return matching
        sink.changed.wait(deadline - monotonic())
        sink.changed.clear()
    return [record for record in sink.records if record.get("request_id") == request_id]


async def test_completed_request_correlates_headers_json_and_safe_route():
    app, sink, _ = instrumented_app()
    async with app_client(app) as client:
        response = await client.get(
            "/__test/items/7?query-secret=secret-query-value",
            headers={
                "X-Request-ID": "trusted.request-7",
                "X-Trace-ID": "a" * 32,
                "traceparent": "00-" + "b" * 32 + "-" + "c" * 16 + "-01",
                "X-PRL-Route": "/client-supplied-route",
                "X-PRL-Outcome": "login_throttled",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trusted.request-7"
    trace_id = response.headers["X-Trace-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert trace_id != "a" * 32
    assert response.headers["X-PRL-Route"] == "/__test/items/{item_id}"
    assert "X-PRL-Outcome" not in response.headers
    matching = _records_for(sink, "trusted.request-7")
    assert len(matching) == 1
    record = matching[0]
    assert record["trace_id"] == trace_id
    assert record["route"] == "/__test/items/{item_id}"
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert record["response_complete"] is True
    assert "secret-query-value" not in json.dumps(sink.records)


async def test_fallback_500_is_observed_once_without_exception_payload():
    app, sink, _ = instrumented_app()
    async with app_client(app, raise_app_exceptions=False) as client:
        response = await client.get("/__test/crash")

    assert response.status_code == 500
    assert response.headers["X-PRL-Route"] == "/__test/crash"
    request_id = response.headers["X-Request-ID"]
    matching = _records_for(sink, request_id)
    assert len(matching) == 1
    assert matching[0]["status"] == 500
    assert matching[0]["error_code"] == "http_5xx"
    assert matching[0]["response_complete"] is True
    encoded = json.dumps(sink.records)
    assert "secret_error_payload" not in encoded
    assert "SQL SELECT" not in encoded


async def test_spoofed_and_duplicate_request_ids_are_replaced_and_correlated():
    app, sink, _ = instrumented_app()
    async with app_client(app, client_address=("192.0.2.5", 12000)) as client:
        response = await client.get(
            "/__test/items/9",
            headers=[
                ("X-Request-ID", "first-client-id"),
                ("X-Request-ID", "second-client-id"),
                ("X-Trace-ID", "9" * 32),
            ],
        )

    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id)
    assert request_id not in {"first-client-id", "second-client-id"}
    assert response.headers["X-Trace-ID"] != "9" * 32
    matching = _records_for(sink, request_id)
    assert len(matching) == 1
    assert matching[0]["request_id"] == request_id
    assert matching[0]["trace_id"] == response.headers["X-Trace-ID"]


async def test_early_auth_body_rejection_has_known_route_and_redacts_query_and_body():
    app, sink, _ = instrumented_app(config=settings(login_max_body_bytes=8))
    async with app_client(app) as client:
        response = await client.post(
            "/api/v1/auth/login?query-secret=early-reject-query",
            content="body-secret-too-large",
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost:3000",
            },
        )

    assert response.status_code == 413
    assert response.headers["X-PRL-Route"] == "/api/v1/auth/login"
    matching = _records_for(sink, response.headers["X-Request-ID"])
    assert len(matching) == 1
    assert matching[0]["route"] == "/api/v1/auth/login"
    assert matching[0]["status"] == 413
    encoded = json.dumps(sink.records)
    assert "early-reject-query" not in encoded
    assert "body-secret-too-large" not in encoded


async def test_404_405_unmatched_and_unknown_method_use_finite_labels():
    app, sink, runtimes = instrumented_app()
    async with app_client(app) as client:
        known_405 = await client.post("/__test/items/77")
        unknown = await client.request("BREW", "/random/private-value-1")
        for identifier in range(2, 22):
            await client.get(f"/random/private-value-{identifier}")
        runtime = runtimes[0]
        lines = runtime.metrics.render().splitlines()
        series_before_scrape_completion = [
            line for line in lines if line.startswith("prl_http_requests_total{")
        ]
        scrape = await client.get("/metrics")

    assert known_405.status_code == 405
    assert known_405.headers["X-PRL-Route"] == "/__test/items/{item_id}"
    assert unknown.status_code == 404
    assert unknown.headers["X-PRL-Route"] == "__unmatched__"
    assert any('method="OTHER"' in line for line in series_before_scrape_completion)
    unmatched_series = [
        line for line in series_before_scrape_completion if 'route="__unmatched__"' in line
    ]
    assert len(unmatched_series) == 2
    assert "random/private-value" not in json.dumps(sink.records)
    assert scrape.status_code == 200


async def test_throttle_marker_is_private_and_unclassified_429_cannot_inherit_it():
    app, _, _ = instrumented_app(config=settings(login_ip_attempts=1))
    async with app_client(app) as client:
        common = {
            "Content-Type": "application/json",
            "Origin": "http://localhost:3000",
            "X-PRL-Outcome": "login_throttled",
        }
        first = await client.post("/api/v1/auth/login", content="{}", headers=common)
        throttled = await client.post("/api/v1/auth/login", content="{}", headers=common)
        unrelated = await client.get("/__test/unclassified-429", headers=common)

    assert first.status_code == 422
    assert throttled.status_code == 429
    assert throttled.headers["X-PRL-Outcome"] == "login_throttled"
    assert unrelated.status_code == 429
    assert "X-PRL-Outcome" not in unrelated.headers


async def test_interrupted_response_is_recorded_once_as_aborted():
    app, sink, runtimes = instrumented_app()
    async with app_client(app) as client:
        with pytest.raises(ResponseInterruptedError):
            await client.get("/__test/stream")
        records = sink.wait_for(1)
        lines = runtimes[0].metrics.render().splitlines()

    assert len(records) == 1
    assert records[0]["status"] == 200
    assert records[0]["response_complete"] is False
    assert "stream_secret_payload" not in json.dumps(records)
    assert any(
        'status="aborted"' in line for line in lines if line.startswith("prl_http_requests_total{")
    )


@pytest.mark.asyncio
async def test_server_span_is_root_even_with_ambient_and_client_trace_context():
    app, sink, _ = instrumented_app()
    ambient_id = int("d" * 32, 16)
    ambient = NonRecordingSpan(
        SpanContext(
            trace_id=ambient_id,
            span_id=int("e" * 16, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
    )
    async with app_client(app) as client:
        with trace.use_span(ambient):
            response = await client.get(
                "/__test/current-trace",
                headers={
                    "traceparent": "00-" + "f" * 32 + "-" + "a" * 16 + "-01",
                    "tracestate": "vendor=state-secret",
                    "baggage": "secret=canary",
                },
            )
            assert trace.get_current_span().get_span_context().trace_id == ambient_id

    payload = response.json()
    assert response.status_code == 200
    assert payload["parent_trace_id"] is None
    assert payload["remote_context_absent"] is True
    assert payload["trace_id"] == response.headers["X-Trace-ID"]
    assert payload["trace_id"] != f"{ambient_id:032x}"
    assert payload["trace_id"] != "f" * 32
    assert len(sink.records) == 1


@pytest.mark.asyncio
async def test_request_without_lifespan_keeps_http_behavior_without_starting_runtime():
    app, _, runtimes = instrumented_app()
    app.openapi()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        response = await client.get("/__test/items/legacy")

    assert response.status_code == 200
    assert response.headers["X-PRL-Route"] == "/__test/items/{item_id}"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-ID"])
    assert runtimes == []


async def test_create_app_instances_have_independent_metrics_and_hide_metrics_from_openapi():
    first, _, first_runtimes = instrumented_app()
    second, _, second_runtimes = instrumented_app()

    @first.get("/__test/only-first", include_in_schema=False)
    async def only_first():
        return {"ok": True}

    async with app_client(first) as first_client:
        assert (await first_client.get("/__test/only-first")).status_code == 200
        first_metrics = await first_client.get("/metrics")
    async with app_client(second) as second_client:
        second_metrics = await second_client.get("/metrics")

    assert first_metrics.status_code == second_metrics.status_code == 200
    assert first_metrics.headers["content-type"] == second_metrics.headers["content-type"]
    assert "/__test/only-first" in first_metrics.text
    assert "/__test/only-first" not in second_metrics.text
    assert first_runtimes[0].metrics.registry is not second_runtimes[0].metrics.registry
    assert "/metrics" not in first.openapi()["paths"]
    assert "/metrics" not in second.openapi()["paths"]


async def test_registered_route_slash_redirect_uses_the_canonical_marker():
    app, _, _ = instrumented_app()
    async with app_client(app, follow_redirects=False) as client:
        response = await client.get("/health/live/")

    assert response.status_code == 307
    assert response.headers["location"].endswith("/health/live")
    assert response.headers["X-PRL-Route"] == "/health/live"
