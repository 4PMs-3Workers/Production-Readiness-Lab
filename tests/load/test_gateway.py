import asyncio
from datetime import UTC, datetime

import pytest
from tools.load.gateway import (
    _gateway_event,
    _prometheus_scalar,
    _runtime_queries,
    _to_ns,
    collect_gateway_window,
    safe_service_url,
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


def test_prometheus_missing_series_is_not_reported_as_zero():
    assert _prometheus_scalar({"status": "success", "data": {"result": []}}) is None
    assert (
        _prometheus_scalar({"status": "success", "data": {"result": [{"value": [1, "4.5"]}]}})
        == 4.5
    )


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


def test_query_times_require_timezone_and_service_url_is_redacted():
    assert _to_ns(datetime(2026, 11, 8, tzinfo=UTC)) > 0
    with pytest.raises(ValueError):
        _to_ns(datetime(2026, 11, 8))
    assert safe_service_url("https://user:secret@example.invalid:8443/path?token=private") == (
        "https://example.invalid:8443"
    )
