"""Candidate-release confirmation and bounded gateway/Prometheus collection."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from tools.deploy.evidence import validate_target_attestation
from tools.load.metrics import GatewayEvent

MAX_GATEWAY_RECORDS = 10_000
MAX_LOKI_ENTRIES_PER_QUERY = 5_000
LOKI_QUERY_SLICE_SECONDS = 30
MAX_TARGET_ATTESTATION_AGE_SECONDS = 900
_ENVIRONMENTS = frozenset({"demo", "local"})


@dataclass(frozen=True)
class GatewayWindow:
    events: list[GatewayEvent]
    complete: bool
    returned_records: int
    truncated: bool
    reason: str | None = None


async def confirm_candidate_release(
    target_url: str,
    loki_url: str,
    release_sha: str,
    *,
    environment: str = "demo",
    timeout_seconds: float = 30,
) -> dict[str, object]:
    """Correlate gateway health and a runtime-bound artifact attestation to the target."""
    start_ns = _to_ns(datetime.now(UTC))
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        response = await client.get(f"{target_url.rstrip('/')}/health/gateway")
    if response.status_code != 200:
        raise RuntimeError("Candidate gateway health check failed")
    request_id = response.headers.get("X-Request-ID")
    if not request_id:
        raise RuntimeError("Candidate gateway health response is missing its request ID")
    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        attestation_response = await client.get(f"{target_url.rstrip('/')}/health/artifacts")
    if attestation_response.status_code != 200:
        raise RuntimeError("Candidate gateway runtime artifact attestation is unavailable")
    attestation_request_id = attestation_response.headers.get("X-Request-ID")
    try:
        attestation = validate_target_attestation(attestation_response.json())
    except (ValueError, TypeError):
        raise RuntimeError("Candidate gateway runtime artifact attestation is invalid") from None
    if attestation["source_sha"] != release_sha or _normalized_origin(
        str(attestation["target_origin"])
    ) != _normalized_origin(target_url):
        raise RuntimeError("Candidate runtime artifact attestation does not match the target")
    attested_at = datetime.fromisoformat(str(attestation["updated_at"]).replace("Z", "+00:00"))
    age_seconds = (datetime.now(UTC) - attested_at.astimezone(UTC)).total_seconds()
    if age_seconds < -60 or age_seconds > MAX_TARGET_ATTESTATION_AGE_SECONDS:
        raise RuntimeError("Candidate runtime artifact attestation is stale")
    if not attestation_request_id:
        raise RuntimeError("Candidate artifact attestation is missing its request ID")
    deadline = datetime.now(UTC).timestamp() + timeout_seconds
    while datetime.now(UTC).timestamp() <= deadline:
        end_ns = _to_ns(datetime.now(UTC))
        records, complete = await _query_loki(
            loki_url,
            f'{_gateway_stream(environment)} | json | __error__ = "" | event="gateway.healthcheck"',
            start_ns,
            end_ns,
            limit=20,
        )
        if not complete:
            raise RuntimeError("Candidate release telemetry is unavailable")
        logged_request_ids = {
            entry.get("request_id")
            for entry in records
            if entry.get("release") == release_sha and entry.get("event") == "gateway.healthcheck"
        }
        if {request_id, attestation_request_id} <= logged_request_ids:
            return {"attestation_sha256": attestation["attestation_sha256"]}
        await _pause(1)
    raise RuntimeError("Gateway did not report the declared candidate release")


async def collect_gateway_window(
    loki_url: str,
    release_sha: str,
    start: datetime,
    end: datetime,
    *,
    environment: str = "demo",
    limit: int = MAX_GATEWAY_RECORDS,
    expected_records: int | None = None,
    expected_request_ids: set[str] | None = None,
) -> GatewayWindow:
    stream = _gateway_stream(environment)
    query = f"{stream} " '| json | __error__ = "" | event="gateway.request"'
    records, complete = await _query_loki_sliced(
        loki_url,
        query,
        max(0, _to_ns(start) - 1_000_000_000),
        _to_ns(end) + 1_000_000_000,
        total_limit=min(limit, MAX_GATEWAY_RECORDS),
    )
    matched_ids: list[object] = []
    duplicate_request_ids = False
    if expected_request_ids is not None:
        records = [record for record in records if record.get("request_id") in expected_request_ids]
        matched_ids = [record.get("request_id") for record in records]
        duplicate_request_ids = len(matched_ids) != len(set(matched_ids))
    events: list[GatewayEvent] = []
    malformed = 0
    mismatched_release = 0
    for record in records:
        if record.get("release") != release_sha:
            mismatched_release += 1
            continue
        try:
            events.append(_gateway_event(record))
        except (KeyError, TypeError, ValueError):
            malformed += 1
    truncated = len(records) >= min(limit, MAX_GATEWAY_RECORDS) or (
        not complete and len(records) >= MAX_LOKI_ENTRIES_PER_QUERY
    )
    count_mismatch = (
        expected_request_ids is not None and set(expected_request_ids) != set(matched_ids)
    ) or (expected_records is not None and len(records) != expected_records)
    complete = (
        complete
        and not truncated
        and malformed == 0
        and mismatched_release == 0
        and not count_mismatch
        and not duplicate_request_ids
    )
    reason = None
    if truncated:
        reason = "gateway_record_limit_reached"
    elif malformed:
        reason = "malformed_gateway_records"
    elif mismatched_release:
        reason = "gateway_release_mismatch"
    elif count_mismatch:
        reason = "gateway_record_count_mismatch"
    elif duplicate_request_ids:
        reason = "duplicate_gateway_request_ids"
    elif not complete:
        reason = "gateway_telemetry_unavailable"
    return GatewayWindow(events, complete, len(records), truncated, reason)


async def collect_runtime_signals(
    prometheus_url: str, duration_seconds: int, *, environment: str = "demo"
) -> dict[str, object]:
    """Read finite worker, pool, and lock signals; absence remains explicitly unavailable."""
    end = datetime.now(UTC)
    range_seconds = max(1, duration_seconds)
    queries = _runtime_queries(range_seconds, environment)
    async with httpx.AsyncClient(timeout=10) as client:
        responses = await _gather_prometheus_queries(client, prometheus_url, queries, end)
    result: dict[str, object] = {"observed_at": end.isoformat(), "signals": {}}
    signals = result["signals"]
    assert isinstance(signals, dict)
    for key, response in responses.items():
        value = _prometheus_scalar(response)
        signals[key] = {
            "value": value,
            "status": "measured" if value is not None else "unavailable",
        }
    return result


async def verify_prometheus_endpoint(
    prometheus_url: str,
    *,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    """Check URL syntax and a harmless query before any candidate business writes."""
    try:
        parsed = urlsplit(prometheus_url)
        _ = parsed.port
    except ValueError:
        raise RuntimeError("Prometheus endpoint URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Prometheus endpoint URL is invalid")
    try:
        async with client_factory(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                f"{prometheus_url.rstrip('/')}/api/v1/query", params={"query": "up"}
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("Prometheus endpoint preflight failed") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("result") if isinstance(data, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or not isinstance(data, dict)
        or not isinstance(rows, list)
    ):
        raise RuntimeError("Prometheus endpoint preflight failed")
    healthy_jobs = {
        metric.get("job")
        for row in rows
        if isinstance(row, dict)
        and isinstance(metric := row.get("metric"), dict)
        and isinstance(row.get("value"), list)
        and len(row["value"]) > 1
        and row["value"][1] == "1"
    }
    if not {"api", "application-otel"} <= healthy_jobs:
        raise RuntimeError("Prometheus API and worker telemetry scrapes must be healthy")


def _runtime_queries(range_seconds: int, environment: str) -> dict[str, str]:
    selected_environment = _environment(environment)
    return {
        "api_scrape_up": 'min(up{job="api"})',
        "worker_telemetry_scrape_up": 'min(up{job="application-otel"})',
        "worker_backlog_max": (
            'max(max_over_time(prl_worker_backlog_count{service_name="worker",'
            f'deployment_environment_name="{selected_environment}"' + f"}}[{range_seconds}s]))"
        ),
        "api_pool_checked_out_max": (
            f'max(max_over_time(prl_db_pool_checked_out{{job="api"}}[{range_seconds}s]))'
        ),
        "api_pool_overflow_max": (
            f'max(max_over_time(prl_db_pool_overflow{{job="api"}}[{range_seconds}s]))'
        ),
        "lock_timeouts": (
            f'sum(increase(prl_db_failures_total{{reason="lock_timeout"}}[{range_seconds}s]))'
        ),
        "pool_timeouts": (
            f'sum(increase(prl_db_failures_total{{reason="pool_timeout"}}[{range_seconds}s]))'
        ),
    }


async def _gather_prometheus_queries(
    client: httpx.AsyncClient, base_url: str, queries: dict[str, str], end: datetime
) -> dict[str, object]:
    import asyncio

    async def query_one(query: str) -> object:
        response = await client.get(
            f"{base_url.rstrip('/')}/api/v1/query",
            params={"query": query, "time": end.timestamp()},
        )
        response.raise_for_status()
        return response.json()

    names = list(queries)
    results = await asyncio.gather(
        *(query_one(queries[name]) for name in names), return_exceptions=True
    )
    return {
        name: None if isinstance(response, Exception) else response
        for name, response in zip(names, results, strict=True)
    }


async def _query_loki(
    base_url: str, query: str, start_ns: int, end_ns: int, *, limit: int
) -> tuple[list[dict[str, object]], bool]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "direction": "forward",
                    "limit": str(min(limit, MAX_LOKI_ENTRIES_PER_QUERY)),
                },
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return [], False
    if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        return [], False
    result = payload["data"].get("result")
    if not isinstance(result, list):
        return [], False
    records: list[dict[str, object]] = []
    for stream in result:
        if not isinstance(stream, dict) or not isinstance(stream.get("values"), list):
            return records, False
        for value in stream["values"]:
            try:
                line = json.loads(value[1])
            except (IndexError, TypeError, json.JSONDecodeError):
                return records, False
            if not isinstance(line, dict):
                return records, False
            records.append(line)
            if len(records) >= min(limit, MAX_LOKI_ENTRIES_PER_QUERY):
                return records, True
    return records, True


async def _query_loki_sliced(
    base_url: str, query: str, start_ns: int, end_ns: int, *, total_limit: int
) -> tuple[list[dict[str, object]], bool]:
    """Query bounded intervals so an individual Loki request stays below its entry cap."""
    records: list[dict[str, object]] = []
    cursor = start_ns
    slice_ns = LOKI_QUERY_SLICE_SECONDS * 1_000_000_000
    while cursor <= end_ns:
        slice_end = min(end_ns, cursor + slice_ns - 1)
        rows, complete = await _query_loki(
            base_url, query, cursor, slice_end, limit=MAX_LOKI_ENTRIES_PER_QUERY
        )
        if not complete or len(rows) >= MAX_LOKI_ENTRIES_PER_QUERY:
            return records + rows[: max(0, total_limit - len(records))], False
        if len(records) + len(rows) > total_limit:
            return records + rows[: max(0, total_limit - len(records))], False
        records.extend(rows)
        cursor = slice_end + 1
    return records, True


def _gateway_event(record: dict[str, object]) -> GatewayEvent:
    route = record["route"]
    method = record["method"]
    status = record["status"]
    duration = record["duration_seconds"]
    classification = record["classification"]
    if isinstance(duration, str):
        try:
            duration = float(duration)
        except ValueError:
            raise ValueError("invalid gateway request duration") from None
    if (
        not isinstance(route, str)
        or route not in _allowed_gateway_routes()
        or len(route) > 128
        or not isinstance(method, str)
        or method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "OTHER"}
        or isinstance(status, bool)
        or not isinstance(status, int)
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
        or not isinstance(classification, str)
    ):
        raise ValueError("invalid gateway access record")
    return GatewayEvent(route, method, status, float(duration), classification)


@lru_cache(maxsize=1)
def _allowed_gateway_routes() -> frozenset[str]:
    """Read only the finite route labels emitted by the checked-out gateway map."""
    route_map = Path(__file__).resolve().parents[2] / "tools/deploy/nginx/route-map.conf"
    try:
        lines = route_map.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError("Unable to load the published gateway route allowlist") from error
    start = next(
        (index for index, line in enumerate(lines) if "$prl_route {" in line),
        None,
    )
    if start is None:
        raise RuntimeError("Published gateway route map is missing")
    routes = {"__unmatched__", "/health/gateway", "/health/artifacts"}
    route_value = re.compile(r'"(?P<route>/[^";]+)"\s*;\s*$')
    for line in lines[start + 1 :]:
        if line.strip() == "}":
            break
        match = route_value.search(line)
        if match is not None:
            routes.add(match.group("route"))
    if len(routes) == 3:
        raise RuntimeError("Published gateway route map contains no route templates")
    return frozenset(routes)


def _prometheus_scalar(response: object) -> float | None:
    if not isinstance(response, dict) or response.get("status") != "success":
        return None
    data = response.get("data")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(result, list) or not result:
        return None
    values = [row.get("value") for row in result if isinstance(row, dict)]
    if not values or not isinstance(values[0], list):
        return None
    try:
        number = float(values[0][1])
    except (IndexError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Loki query times must be timezone-aware")
    return int(value.astimezone(UTC).timestamp() * 1_000_000_000)


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    default_port = 80 if scheme == "http" else 443
    return f"{scheme}://{host}" + (f":{port}" if port is not None and port != default_port else "")


def _environment(environment: str) -> str:
    if environment not in _ENVIRONMENTS:
        raise ValueError("Environment must be a declared lab environment")
    return environment


def _gateway_stream(environment: str) -> str:
    return (
        '{service_name="gateway",deployment_environment_name="' + _environment(environment) + '"}'
    )


async def _pause(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def safe_service_url(url: str) -> str:
    """Return a credential-free service origin suitable for bounded reports."""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
    return urlunsplit((parsed.scheme, authority, "", "", ""))
