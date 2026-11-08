import asyncio
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from tools.load.gateway import (
    _gateway_event,
    _prometheus_scalar,
    _runtime_queries,
    _to_ns,
    collect_gateway_window,
    confirm_candidate_release,
    safe_service_url,
    verify_prometheus_endpoint,
)


def test_gateway_event_accepts_only_sanitized_route_fields():
    result = _gateway_event(
        {
            "route": "/api/v1/appointments/{appointment_id}",
            "method": "GET",
            "status": 409,
            "duration_seconds": "0.200",
            "classification": "excluded",
        }
    )
    assert result.status == 409
    assert result.classification == "excluded"

    for unsafe_route in (
        "/api/v1/appointments?student=private",
        "/api/v1/admin/accounts/alice@example.com",
    ):
        with pytest.raises(ValueError):
            _gateway_event(
                {
                    "route": unsafe_route,
                    "method": "GET",
                    "status": 200,
                    "duration_seconds": 0.1,
                    "classification": "success",
                }
            )


def test_gateway_window_is_incomplete_when_release_does_not_match(monkeypatch):
    async def fake_query(*args, **kwargs):
        return (
            [
                {
                    "release": "b" * 40,
                    "route": "/api/v1/auth/me",
                    "method": "GET",
                    "status": 200,
                    "duration_seconds": 0.1,
                    "classification": "success",
                }
            ],
            True,
        )

    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    result = asyncio.run(
        collect_gateway_window(
            "http://localhost:3100",
            "a" * 40,
            datetime(2026, 11, 8, tzinfo=UTC),
            datetime(2026, 11, 8, 0, 10, tzinfo=UTC),
        )
    )
    assert result.complete is False
    assert result.events == []
    assert result.reason == "gateway_release_mismatch"


def test_gateway_window_is_incomplete_when_logs_drop_expected_records(monkeypatch):
    async def fake_query(*args, **kwargs):
        return (
            [
                {
                    "release": "a" * 40,
                    "route": "/api/v1/auth/me",
                    "method": "GET",
                    "status": 200,
                    "duration_seconds": 0.1,
                    "classification": "success",
                }
            ],
            True,
        )

    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    result = asyncio.run(
        collect_gateway_window(
            "http://localhost:3100",
            "a" * 40,
            datetime(2026, 11, 8, tzinfo=UTC),
            datetime(2026, 11, 8, 0, 10, tzinfo=UTC),
            expected_records=2,
        )
    )
    assert result.complete is False
    assert result.reason == "gateway_record_count_mismatch"


def test_gateway_window_matches_exact_request_ids_not_an_unrelated_replacement(monkeypatch):
    async def fake_query(*args, **kwargs):
        return (
            [
                {
                    "release": "a" * 40,
                    "request_id": "unrelated",
                    "route": "/api/v1/auth/me",
                    "method": "GET",
                    "status": 200,
                    "duration_seconds": 0.1,
                    "classification": "success",
                }
            ],
            True,
        )

    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    result = asyncio.run(
        collect_gateway_window(
            "http://localhost:3100",
            "a" * 40,
            datetime(2026, 11, 8, tzinfo=UTC),
            datetime(2026, 11, 8, 0, 0, 2, tzinfo=UTC),
            expected_records=1,
            expected_request_ids={"expected"},
        )
    )

    assert result.complete is False
    assert result.returned_records == 0
    assert result.reason == "gateway_record_count_mismatch"


def test_gateway_window_applies_workload_budget_after_matching_request_ids(monkeypatch):
    slices = 0
    event = {
        "release": "a" * 40,
        "route": "/api/v1/auth/me",
        "method": "GET",
        "status": 200,
        "duration_seconds": 0.1,
        "classification": "success",
    }

    async def fake_query(_base, _query, _start, _end, *, limit):
        nonlocal slices
        assert limit <= 5_000
        start_index = slices * 600
        slices += 1
        expected = [
            {**event, "request_id": f"expected-{index}"}
            for index in range(start_index, min(start_index + 600, 12_000))
        ]
        unrelated = [{**event, "request_id": f"noise-{slices}-{index}"} for index in range(500)]
        return unrelated + expected, True

    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    ids = {f"expected-{index}" for index in range(12_000)}
    result = asyncio.run(
        collect_gateway_window(
            "http://localhost:3100",
            "a" * 40,
            datetime(2026, 11, 8, tzinfo=UTC),
            datetime(2026, 11, 8, 0, 10, tzinfo=UTC),
            expected_records=len(ids),
            expected_request_ids=ids,
        )
    )

    assert result.complete is True
    assert result.returned_records == len(ids)
    assert slices == 21


def test_release_preflight_pads_loki_start_for_whole_second_timestamps(monkeypatch):
    from tools.deploy.evidence import create_target_attestation, create_v2

    manifest = create_v2(
        "a" * 40,
        "https",
        False,
        "e" * 64,
        "evidence",
        "passed",
        {
            name: {"id": "sha256:" + digest * 64, "digests": []}
            for name, digest in (("api", "a"), ("frontend", "b"), ("worker", "c"), ("gateway", "d"))
        },
        deployment_state="active",
        target_origin="https://candidate.invalid",
    )
    attestation = create_target_attestation(manifest)

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            request_id = (
                "probe-request" if url.endswith("/health/gateway") else "attestation-request"
            )
            if request_id == "probe-request":
                return httpx.Response(200, headers={"X-Request-ID": request_id})
            return httpx.Response(200, json=attestation, headers={"X-Request-ID": request_id})

    observed = {}
    ticks = iter((100_250_000_000, 101_250_000_000))
    monkeypatch.setattr("tools.load.gateway._to_ns", lambda _value: next(ticks))

    async def fake_query(_base, _query, start, end, *, limit):
        observed["bounds"] = start, end
        return (
            [
                {"release": "a" * 40, "event": "gateway.healthcheck", "request_id": rid}
                for rid in ("probe-request", "attestation-request")
            ],
            True,
        )

    monkeypatch.setattr("tools.load.gateway.httpx.AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)

    asyncio.run(
        confirm_candidate_release("https://candidate.invalid", "https://loki.invalid", "a" * 40)
    )

    assert observed["bounds"][0] <= 100_000_000_000
    assert observed["bounds"][1] >= 102_000_000_000


def test_health_probe_requires_the_matching_gateway_log_request_id(monkeypatch):
    from tools.deploy.evidence import create_target_attestation, create_v2

    manifest = create_v2(
        "a" * 40,
        "https",
        False,
        "e" * 64,
        "evidence",
        "passed",
        {
            name: {"id": "sha256:" + digest * 64, "digests": []}
            for name, digest in (("api", "a"), ("frontend", "b"), ("worker", "c"), ("gateway", "d"))
        },
        deployment_state="active",
        target_origin="https://candidate.invalid",
    )
    attestation = create_target_attestation(manifest)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            self.calls += 1
            if url.endswith("/health/gateway"):
                return httpx.Response(200, headers={"X-Request-ID": "probe-request"})
            return httpx.Response(
                200,
                json=attestation,
                headers={"X-Request-ID": "attestation-request"},
            )

    async def fake_query(*_args, **_kwargs):
        return (
            [
                {
                    "release": "a" * 40,
                    "event": "gateway.healthcheck",
                    "request_id": "different-request",
                }
            ],
            True,
        )

    async def no_wait(*_args):
        return None

    monkeypatch.setattr("tools.load.gateway.httpx.AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    monkeypatch.setattr("tools.load.gateway._pause", no_wait)

    with pytest.raises(RuntimeError, match="did not report"):
        asyncio.run(
            confirm_candidate_release(
                "https://candidate.invalid",
                "https://loki.invalid",
                "a" * 40,
                timeout_seconds=0.01,
            )
        )


def test_health_probe_accepts_matching_runtime_manifest_and_log_ids(monkeypatch):
    from tools.deploy.evidence import create_target_attestation, create_v2

    manifest = create_v2(
        "a" * 40,
        "https",
        False,
        "e" * 64,
        "evidence",
        "passed",
        {
            name: {"id": "sha256:" + digest * 64, "digests": []}
            for name, digest in (("api", "a"), ("frontend", "b"), ("worker", "c"), ("gateway", "d"))
        },
        deployment_state="active",
        target_origin="https://candidate.invalid",
    )
    attestation = create_target_attestation(manifest)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            self.calls += 1
            if url.endswith("/health/gateway"):
                return httpx.Response(200, headers={"X-Request-ID": "probe-request"})
            return httpx.Response(
                200,
                json=attestation,
                headers={"X-Request-ID": "attestation-request"},
            )

    async def fake_query(*_args, **_kwargs):
        return (
            [
                {
                    "release": "a" * 40,
                    "event": "gateway.healthcheck",
                    "request_id": request_id,
                }
                for request_id in ("probe-request", "attestation-request")
            ],
            True,
        )

    monkeypatch.setattr(
        "tools.load.gateway.httpx.AsyncClient", lambda **kwargs: FakeClient(**kwargs)
    )
    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)

    result = asyncio.run(
        confirm_candidate_release(
            "https://candidate.invalid",
            "https://loki.invalid",
            "a" * 40,
        )
    )

    assert result == {"attestation_sha256": attestation["attestation_sha256"]}


def test_loki_query_slices_never_request_more_than_server_entry_limit(monkeypatch):
    from tools.load.gateway import MAX_LOKI_ENTRIES_PER_QUERY, _query_loki_sliced

    limits = []

    async def fake_query(_base, _query, start, end, *, limit):
        limits.append(limit)
        assert end >= start
        return ([{"request_id": str(len(limits))}], True)

    monkeypatch.setattr("tools.load.gateway._query_loki", fake_query)
    records, complete = asyncio.run(
        _query_loki_sliced(
            "http://localhost:3100", '{service="gateway"}', 0, 60_000_000_000, total_limit=10_000
        )
    )

    assert complete is True
    assert len(records) == 3
    assert limits == [MAX_LOKI_ENTRIES_PER_QUERY] * 3


def test_prometheus_missing_series_is_not_reported_as_zero():
    assert _prometheus_scalar({"status": "success", "data": {"result": []}}) is None
    assert (
        _prometheus_scalar({"status": "success", "data": {"result": [{"value": [1, "4.5"]}]}})
        == 4.5
    )


def test_prometheus_preflight_checks_query_api_before_workload_writes():
    async def run():
        def client_factory(**kwargs):
            def handle(request):
                assert request.url.path == "/api/v1/query"
                assert request.url.params["query"] == "up"
                return httpx.Response(
                    200,
                    json={
                        "status": "success",
                        "data": {
                            "resultType": "vector",
                            "result": [
                                {"metric": {"job": "api"}, "value": [1, "1"]},
                                {"metric": {"job": "application-otel"}, "value": [1, "1"]},
                            ],
                        },
                    },
                )

            return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

        await verify_prometheus_endpoint(
            "https://prometheus.example.invalid", client_factory=client_factory
        )

    asyncio.run(run())


def test_prometheus_preflight_rejects_unreachable_and_credential_urls():
    async def run():
        def unavailable_client(**kwargs):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
                **kwargs,
            )

        with pytest.raises(RuntimeError, match="preflight failed"):
            await verify_prometheus_endpoint(
                "https://prometheus.example.invalid", client_factory=unavailable_client
            )
        with pytest.raises(RuntimeError, match="URL is invalid"):
            await verify_prometheus_endpoint("https://user:secret@prometheus.example.invalid")
        with pytest.raises(RuntimeError, match="scrapes must be healthy"):
            await verify_prometheus_endpoint(
                "https://prometheus.example.invalid",
                client_factory=lambda **kwargs: httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda _request: httpx.Response(
                            200,
                            json={"status": "success", "data": {"result": []}},
                        )
                    ),
                    **kwargs,
                ),
            )

    asyncio.run(run())


def test_worker_backlog_signal_captures_peak_over_the_observed_window():
    queries = _runtime_queries(600, "demo")
    query = queries["worker_backlog_max"]
    assert query == (
        'max(max_over_time(prl_worker_backlog_count{service_name="worker",'
        'deployment_environment_name="demo"}[600s]))'
    )
    assert queries["api_pool_checked_out_max"] == (
        'max(max_over_time(prl_db_pool_checked_out{job="api"}[600s]))'
    )
    assert queries["api_pool_overflow_max"] == (
        'max(max_over_time(prl_db_pool_overflow{job="api"}[600s]))'
    )
    assert "or vector(0)" not in queries["lock_timeouts"]
    assert "or vector(0)" not in queries["pool_timeouts"]
    assert 'min_over_time(up{job="api"}[600s])' in queries["api_scrape_up"]
    assert 'count_over_time(up{job="application-otel"}[600s])' in queries["worker_scrape_samples"]


def test_runtime_collection_uses_workload_end_after_delayed_collection(monkeypatch):
    from tools.load.gateway import collect_runtime_signals

    start = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 8, 0, 10, tzinfo=UTC)
    observed = {}

    async def fake_gather(_client, _url, queries, query_end):
        observed["queries"] = queries
        observed["end"] = query_end
        return {
            name: {"status": "success", "data": {"result": [{"value": ["0", "40"]}]}}
            for name in queries
        }

    monkeypatch.setattr("tools.load.gateway._gather_prometheus_queries", fake_gather)
    result = asyncio.run(collect_runtime_signals("https://prom.invalid", start, end))

    observed_queries = cast(dict[str, str], observed["queries"])
    assert observed["end"] == end
    assert "[600s]" in observed_queries["worker_backlog_max"]
    assert result["window_start"] == start.isoformat()
    assert result["window_end"] == end.isoformat()
    result_signals = cast(dict[str, dict[str, object]], result["signals"])
    assert result_signals["worker_scrape_samples"]["status"] == "measured"

    async def sparse_gather(_client, _url, queries, _query_end):
        return {
            name: {
                "status": "success",
                "data": {"result": [{"value": ["0", "39" if "scrape_samples" in name else "40"]}]},
            }
            for name in queries
        }

    monkeypatch.setattr("tools.load.gateway._gather_prometheus_queries", sparse_gather)
    sparse = asyncio.run(collect_runtime_signals("https://prom.invalid", start, end))
    sparse_signals = cast(dict[str, dict[str, object]], sparse["signals"])
    assert sparse_signals["api_scrape_samples"]["status"] == "unavailable"
    assert sparse_signals["worker_backlog_max"]["status"] == "unavailable"


def test_query_times_require_timezone_and_service_url_is_redacted():
    assert _to_ns(datetime(2026, 11, 8, tzinfo=UTC)) > 0
    with pytest.raises(ValueError):
        _to_ns(datetime(2026, 11, 8))
    assert safe_service_url("https://user:secret@example.invalid:8443/path?token=private") == (
        "https://example.invalid:8443"
    )
