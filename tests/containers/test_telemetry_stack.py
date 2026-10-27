"""Real bounded container checks for the opt-in observability deployment."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import urlencode, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tests.containers.observability_review_evidence import (
    build_failure_diagnostics,
    build_runtime_evidence,
    collect_config_hashes,
    write_evidence_documents,
)
from tests.containers.test_compose_acceptance import (
    OPS_EDGE_TEST_GATEWAY,
    OPS_EDGE_TEST_SUBNET,
    AcceptanceFailure,
    ComposeAcceptance,
    latest_worker_progress,
)
from tests.containers.test_compose_acceptance import (
    invoke as acceptance_invoke,
)
from tests.containers.test_compose_acceptance import (
    json_output as acceptance_json,
)
from tests.containers.test_compose_acceptance import (
    require_success as acceptance_success,
)
from tests.containers.worker_alert_probe import (
    MonotonicDeadline,
    acquire_pending_proposal_row_lock,
    active_worker_alert_identity,
    capture_receiver_snapshot_before_recovery,
    normalize_alertmanager_timestamp,
    run_bounded_mysql_statement,
    wait_until_deadline,
)

ROOT = Path(__file__).resolve().parents[2]
NGINX_IMAGE = (
    "nginx:stable-alpine@sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576"
)
GATEWAY_SUBNET = ipaddress.ip_network("172.30.34.0/29")
GATEWAY_API_IP = "172.30.34.3"
GATEWAY_IP = "172.30.34.2"
OWNER_LABEL = "org.prl.task6.owner"

OPS_PROBE_STAGES = (
    "health",
    "anonymous_org",
    "admin_org",
    "datasources",
    "ops_business",
    "ops_health",
    "dashboards",
)
OPS_PROBE_RESPONSE_KINDS = {"dict", "list", "null", "scalar", "invalid", "unavailable"}
OPS_PROBE_FAILURE_CATEGORIES = {
    "certificate_validation",
    "compose_service_missing",
    "connection_refused",
    "container_not_running",
    "missing_file",
    "mount_configuration",
    "permission_denied",
    "python_traceback",
    "request_error",
    "timeout",
    "tls_error",
    "transport_error",
    "unclassified_failure",
}
OPS_PROBE_DATASOURCE_UIDS = {"prl-loki", "prl-prometheus", "prl-tempo"}
OPS_PROBE_DASHBOARD_UIDS = {
    "prl-gateway",
    "prl-api-errors",
    "prl-db-pool",
    "prl-worker-progress",
    "prl-telemetry-loss",
}
WORKER_METRIC_SETTLE_SECONDS = 16.0  # One full 15-second Prometheus scrape interval plus margin.


def fullstack_ops_peer_cidrs() -> str:
    """Return the exact test-only peers for loopback probes and the published edge."""
    return f"127.0.0.1/32,{OPS_EDGE_TEST_GATEWAY}/32"


def fullstack_ops_edge_network_overlay() -> str:
    """Pin only the test acceptance edge network so its host peer is deterministic."""
    return yaml.safe_dump(
        {
            "services": {},
            "networks": {
                "edge": {
                    "ipam": {
                        "config": [
                            {
                                "subnet": str(OPS_EDGE_TEST_SUBNET),
                                "gateway": str(OPS_EDGE_TEST_GATEWAY),
                            }
                        ]
                    }
                }
            },
        },
        sort_keys=True,
    )


def restart_worker_while_database_is_unavailable(
    compose: Callable[..., subprocess.CompletedProcess[str]],
    inspect_service: Callable[[str], dict[str, object]],
    *,
    worker_container_id: str,
    mysql_container_id: str,
    capture_baseline: Callable[[], dict[str, dict[str, object]]],
    on_worker_stopped: Callable[[], None] | None = None,
) -> dict[str, dict[str, object]]:
    """Restart the existing worker container while MySQL is stopped."""

    def require_status(service: str, container_id: str, expected_status: str) -> None:
        record = inspect_service(service)
        state = _object_mapping(record.get("State"))
        if record.get("Id") != container_id or state.get("Status") != expected_status:
            raise AcceptanceFailure(
                f"The UUID-owned {service} container did not remain {expected_status}"
            )

    acceptance_success(
        compose("stop", "--timeout", "10", "worker", timeout=30),
        "The UUID-owned worker could not stop for the same-slot observation",
    )
    require_status("worker", worker_container_id, "exited")
    if on_worker_stopped is not None:
        on_worker_stopped()
    acceptance_success(
        compose("stop", "--timeout", "10", "mysql", timeout=30),
        "The UUID-owned database could not stop for the same-slot observation",
    )
    require_status("worker", worker_container_id, "exited")
    require_status("mysql", mysql_container_id, "exited")
    baseline = capture_baseline()
    acceptance_success(
        compose("up", "-d", "--no-deps", "--no-recreate", "worker", timeout=45),
        "The stopped worker could not start while MySQL was unavailable",
    )
    require_status("worker", worker_container_id, "running")
    require_status("mysql", mysql_container_id, "exited")
    return baseline


RECEIVER_HEALTH_TARGETS = frozenset(
    {"alertmanager", "collector", "grafana", "loki", "prometheus", "tempo"}
)
RECEIVER_HEALTH_ERROR_CATEGORIES = {
    "InvalidEnvelope",
    "SubprocessError",
    "TransportError",
}
RECEIVER_COMPONENT_ERROR_CATEGORIES = {
    "connection_refused",
    "dns",
    "timeout",
    "transport",
}
RECEIVER_COMPONENT_PROBE_SCRIPT = r"""import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from app.observability.alert_receiver import TELEMETRY_TARGETS

expected_ops_host = sys.argv[1]
target_names = {name for name, _url in TELEMETRY_TARGETS}

def error_category(error):
    candidates = (error, getattr(error, "reason", None))
    if any(isinstance(candidate, TimeoutError) for candidate in candidates):
        return "timeout"
    if any(isinstance(candidate, socket.gaierror) for candidate in candidates):
        return "dns"
    if any(isinstance(candidate, ConnectionRefusedError) for candidate in candidates):
        return "connection_refused"
    return "transport"

def observe_receiver_health():
    started = time.monotonic()
    status = None
    raw = b""
    error = None
    try:
        response = urllib.request.urlopen("http://127.0.0.1:8088/health/telemetry", timeout=5)
        status = response.status
        raw = response.read(4097)
        response.close()
    except urllib.error.HTTPError as failure:
        status = failure.code
        raw = failure.read(4097)
        failure.close()
    except (OSError, urllib.error.URLError, TimeoutError) as failure:
        error = error_category(failure)

    truncated = len(raw) > 4096
    valid = False
    decoded = None
    try:
        decoded = json.loads(raw[:4096])
        valid = True
    except (ValueError, UnicodeDecodeError):
        pass
    body = None
    unhealthy = []
    unknown_count = 0
    shape_ok = False
    if isinstance(decoded, dict):
        raw_status = decoded.get("status")
        raw_unhealthy = decoded.get("unhealthy")
        shape_ok = isinstance(raw_unhealthy, list)
        if shape_ok:
            for name in raw_unhealthy:
                if isinstance(name, str) and name in target_names:
                    unhealthy.append(name)
                else:
                    unknown_count += 1
        body = {
            "status": (
                raw_status
                if isinstance(raw_status, str) and raw_status in {"passed", "failed"}
                else None
            ),
            "unhealthy": sorted(set(unhealthy)) if shape_ok else None,
        }
    observation = {
        "http_status": status,
        "body": body,
        "json_valid": valid,
        "truncated": truncated,
        "error_category": error,
        "elapsed_ms": min(30000, max(0, int((time.monotonic() - started) * 1000))),
        "unhealthy_shape_ok": shape_ok,
        "unknown_unhealthy_count": unknown_count,
    }
    passed = (
        status == 200
        and valid
        and not truncated
        and error is None
        and isinstance(body, dict)
        and body.get("status") == "passed"
        and shape_ok
        and unhealthy == []
        and unknown_count == 0
    )
    return observation, passed, sorted(set(unhealthy)), unknown_count

poll_attempts = 0
poll_healthy = False
poll_unhealthy = []
unknown_poll_failure_count = 0
poll_started = time.monotonic()
deadline = poll_started + 60
receiver_health = None
while poll_attempts < 120 and time.monotonic() < deadline:
    poll_attempts += 1
    receiver_health, poll_healthy, poll_unhealthy, unknown_poll_failure_count = (
        observe_receiver_health()
    )
    if poll_healthy:
        if time.monotonic() >= deadline:
            poll_healthy = False
        else:
            break
    time.sleep(0.5)
poll_elapsed_ms = min(120000, max(0, int((time.monotonic() - poll_started) * 1000)))
poll_budget_exceeded = time.monotonic() >= deadline
if poll_budget_exceeded:
    poll_healthy = False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None

no_redirect = urllib.request.build_opener(NoRedirect())
target_results = {}
for name, url in TELEMETRY_TARGETS:
    started = time.monotonic()
    initial_status = None
    redirect = False
    redirect_to_ops_host = False
    try:
        with no_redirect.open(url, timeout=0.5) as response:
            initial_status = response.status
    except urllib.error.HTTPError as error:
        initial_status = error.code
        redirect = 300 <= error.code < 400
        location = error.headers.get("Location", "")
        redirect_to_ops_host = urllib.parse.urlparse(location).hostname == expected_ops_host
        error.close()
    except (OSError, urllib.error.URLError, TimeoutError):
        pass

    final_status = None
    final_host_is_ops = False
    follow_error_category = None
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            final_status = response.status
            final_host_is_ops = (
                urllib.parse.urlparse(response.geturl()).hostname == expected_ops_host
            )
    except urllib.error.HTTPError as error:
        final_status = error.code
        final_host_is_ops = urllib.parse.urlparse(error.geturl()).hostname == expected_ops_host
        error.close()
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        follow_error_category = error_category(error)

    target_results[name] = {
        "initial_status": initial_status,
        "redirect": redirect,
        "redirect_to_ops_host": redirect_to_ops_host,
        "final_status": final_status,
        "final_host_is_ops": final_host_is_ops,
        "follow_error_category": follow_error_category,
        "elapsed_ms": min(30000, max(0, int((time.monotonic() - started) * 1000))),
    }

receiver_health, _final_healthy, _final_unhealthy, _final_unknown_count = (
    observe_receiver_health()
)
if receiver_health is None:
    receiver_health = {
        "http_status": None,
        "body": None,
        "json_valid": False,
        "truncated": False,
        "error_category": "TransportError",
        "elapsed_ms": 0,
        "unhealthy_shape_ok": False,
        "unknown_unhealthy_count": 0,
    }
receiver_health["total_elapsed_ms"] = poll_elapsed_ms
receiver_health["budget_exceeded"] = poll_budget_exceeded

result = {
    "poll_attempts": poll_attempts,
    "poll_elapsed_ms": poll_elapsed_ms,
    "poll_budget_exceeded": poll_budget_exceeded,
    "poll_unhealthy": poll_unhealthy,
    "poll_healthy": poll_healthy,
    "unknown_poll_failure_count": unknown_poll_failure_count,
    "receiver_health": receiver_health,
    "targets": target_results,
}
print(json.dumps(result, separators=(",", ":")))
"""


def safe_receiver_health_summary(observation: dict[str, object]) -> dict[str, object]:
    """Keep receiver health failure evidence finite and free of arbitrary body data."""
    if not isinstance(observation, dict):
        observation = {}
    raw_status = observation.get("http_status")
    http_status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else None
    json_valid = observation.get("json_valid") is True
    body = observation.get("body")
    raw_error_category = observation.get("error_category")
    if raw_error_category is None:
        error_category = None
    elif (
        isinstance(raw_error_category, str)
        and raw_error_category in RECEIVER_HEALTH_ERROR_CATEGORIES
    ):
        error_category = raw_error_category
    else:
        error_category = "Other"
    if not json_valid:
        kind = "invalid"
    elif isinstance(body, dict):
        kind = "dict"
    elif isinstance(body, list):
        kind = "list"
    elif body is None:
        kind = "null"
    else:
        kind = "scalar"
    if http_status is None and error_category is not None:
        kind = "unavailable"

    body_status = None
    unhealthy: list[str] = []
    unknown_unhealthy_count = 0
    unhealthy_shape_ok = False
    if isinstance(body, dict):
        raw_body_status = body.get("status")
        if isinstance(raw_body_status, str) and raw_body_status in {"passed", "failed"}:
            body_status = raw_body_status
        raw_unhealthy = body.get("unhealthy")
        if isinstance(raw_unhealthy, list):
            unhealthy_shape_ok = True
            seen: set[str] = set()
            for item in raw_unhealthy:
                if isinstance(item, str) and item in RECEIVER_HEALTH_TARGETS:
                    seen.add(item)
                else:
                    unknown_unhealthy_count += 1
            unhealthy = sorted(seen)

    raw_elapsed = observation.get("elapsed_ms")
    elapsed_ms = min(30_000, raw_elapsed) if type(raw_elapsed) is int and raw_elapsed >= 0 else None
    raw_total_elapsed = observation.get("total_elapsed_ms")
    total_elapsed_ms = (
        min(120_000, raw_total_elapsed)
        if type(raw_total_elapsed) is int and raw_total_elapsed >= 0
        else None
    )
    raw_budget_exceeded = observation.get("budget_exceeded")
    budget_exceeded = raw_budget_exceeded if isinstance(raw_budget_exceeded, bool) else None
    return {
        "error_category": error_category,
        "http_status": http_status,
        "json_kind": kind,
        "json_valid": json_valid,
        "elapsed_ms": elapsed_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "budget_exceeded": budget_exceeded,
        "status": body_status,
        "truncated": observation.get("truncated") is True,
        "unhealthy": unhealthy,
        "unhealthy_shape_ok": unhealthy_shape_ok,
        "unknown_unhealthy_count": unknown_unhealthy_count,
    }


def receiver_health_is_passed(summary: dict[str, object]) -> bool:
    return (
        isinstance(summary, dict)
        and summary.get("error_category") is None
        and summary.get("http_status") == 200
        and summary.get("json_kind") == "dict"
        and summary.get("json_valid") is True
        and type(summary.get("total_elapsed_ms")) is int
        and 0 <= summary.get("total_elapsed_ms") <= 60_000
        and summary.get("budget_exceeded") is False
        and summary.get("status") == "passed"
        and summary.get("truncated") is False
        and summary.get("unhealthy") == []
        and summary.get("unhealthy_shape_ok") is True
        and type(summary.get("unknown_unhealthy_count")) is int
        and summary.get("unknown_unhealthy_count") == 0
    )


def _receiver_status_key(item: dict[str, str]) -> tuple[str, str, str, str, str] | None:
    status = item.get("status")
    severity = item.get("severity")
    received_at = item.get("received_at")
    if not all(isinstance(value, str) for value in (status, severity, received_at)):
        return None
    fingerprint = item.get("fingerprint")
    starts_at = item.get("starts_at")
    if fingerprint is None and starts_at is None:
        fingerprint = starts_at = ""
    elif (
        not isinstance(fingerprint, str)
        or not re.fullmatch(r"[0-9a-f]{16,64}", fingerprint)
        or not isinstance(starts_at, str)
    ):
        return None
    else:
        try:
            if normalize_alertmanager_timestamp(starts_at) != starts_at:
                return None
        except ValueError:
            return None
    return status, severity, received_at, fingerprint, starts_at


def new_alert_statuses_since_snapshot(
    baseline: list[dict[str, str]],
    current: list[dict[str, str]],
    *,
    status: str,
    severity: str,
) -> list[dict[str, str]]:
    """Return matching receiver summaries newly present after the saved snapshot."""
    baseline_counts = Counter(
        key for item in baseline if (key := _receiver_status_key(item)) is not None
    )
    new_records: list[dict[str, str]] = []
    for item in current:
        key = _receiver_status_key(item)
        if key is None or key[:2] != (status, severity):
            continue
        if baseline_counts[key] > 0:
            baseline_counts[key] -= 1
        else:
            new_records.append(item)
    return new_records


def gateway_sli_firing_truth_table_is_active(
    successful_requests: float | None,
    fast_successful_requests: float | None,
    fast_success_ratio: float | None,
    exact_alert_predicate: float | None,
) -> bool:
    return (
        successful_requests is not None
        and successful_requests >= 20
        and fast_successful_requests is not None
        and fast_success_ratio is not None
        and fast_success_ratio < 0.95
        and exact_alert_predicate is not None
    )


def test_alert_receiver_status_summary_preserves_safe_received_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FullStackRuntime.__new__(FullStackRuntime)
    received_at = "2026-10-26T10:00:00.000Z"
    monkeypatch.setattr(
        runtime,
        "api_json",
        lambda _url, *, deadline=None: {
            "alerts": [
                {
                    "alertname": "GatewayFastSuccessBelowSLO",
                    "severity": "warning",
                    "status": "firing",
                    "received_at": received_at,
                }
            ]
        },
    )

    assert runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO") == [
        {"status": "firing", "severity": "warning", "received_at": received_at}
    ]


def test_new_firing_delivery_requires_record_after_receiver_snapshot() -> None:
    stale = {
        "status": "firing",
        "severity": "warning",
        "received_at": "2026-10-26T10:00:00.000Z",
    }
    fresh = {
        "status": "firing",
        "severity": "warning",
        "received_at": "2026-10-26T10:02:00.000Z",
    }
    baseline = [stale]

    assert (
        new_alert_statuses_since_snapshot(baseline, [stale], status="firing", severity="warning")
        == []
    )
    assert new_alert_statuses_since_snapshot(
        baseline, [stale, fresh], status="firing", severity="warning"
    ) == [fresh]
    assert (
        new_alert_statuses_since_snapshot(
            baseline,
            [
                stale,
                {
                    "status": "resolved",
                    "severity": "warning",
                    "received_at": "2026-10-26T10:02:00.000Z",
                },
            ],
            status="firing",
            severity="warning",
        )
        == []
    )


def test_new_resolved_delivery_requires_record_after_recovery_snapshot() -> None:
    stale = {
        "status": "resolved",
        "severity": "warning",
        "received_at": "2026-10-26T10:00:00.000Z",
    }
    fresh = {
        "status": "resolved",
        "severity": "warning",
        "received_at": "2026-10-26T10:03:00.000Z",
    }
    baseline = [stale]

    assert (
        new_alert_statuses_since_snapshot(baseline, [stale], status="resolved", severity="warning")
        == []
    )
    assert new_alert_statuses_since_snapshot(
        baseline, [stale, fresh], status="resolved", severity="warning"
    ) == [fresh]
    assert (
        new_alert_statuses_since_snapshot(
            baseline,
            [
                {
                    "status": "firing",
                    "severity": "warning",
                    "received_at": "2026-10-26T10:03:00.000Z",
                }
            ],
            status="resolved",
            severity="warning",
        )
        == []
    )


def test_gateway_sli_firing_snapshot_rejects_subthreshold_values() -> None:
    assert gateway_sli_firing_truth_table_is_active(19.0, 19.0, 1.0, None) is False
    assert gateway_sli_firing_truth_table_is_active(20.0, 18.0, 0.9, 0.9) is True


def safe_receiver_component_summary(observation: dict[str, object]) -> dict[str, object]:
    """Retain only fixed backend names and bounded HTTP outcomes from the local probe."""
    if not isinstance(observation, dict):
        observation = {}
    raw_health = observation.get("receiver_health")
    health = (
        safe_receiver_health_summary(raw_health)
        if isinstance(raw_health, dict)
        else {"available": False}
    )
    if isinstance(raw_health, dict):
        raw_shape_ok = raw_health.get("unhealthy_shape_ok")
        raw_unknown_count = raw_health.get("unknown_unhealthy_count")
        if isinstance(raw_shape_ok, bool):
            health["unhealthy_shape_ok"] = raw_shape_ok
        if type(raw_unknown_count) is int and raw_unknown_count >= 0:
            health["unknown_unhealthy_count"] = min(1000, raw_unknown_count)
    raw_attempts = observation.get("poll_attempts")
    poll_attempts = (
        min(120, raw_attempts) if type(raw_attempts) is int and raw_attempts >= 0 else None
    )
    raw_poll_elapsed = observation.get("poll_elapsed_ms")
    poll_elapsed_ms = (
        min(120_000, raw_poll_elapsed)
        if type(raw_poll_elapsed) is int and raw_poll_elapsed >= 0
        else None
    )
    raw_poll_budget_exceeded = observation.get("poll_budget_exceeded")
    poll_budget_exceeded = (
        raw_poll_budget_exceeded if isinstance(raw_poll_budget_exceeded, bool) else None
    )

    poll_unhealthy: set[str] = set()
    unknown_poll_failure_count = 0
    raw_poll_unhealthy = observation.get("poll_unhealthy")
    if isinstance(raw_poll_unhealthy, list):
        for name in raw_poll_unhealthy:
            if isinstance(name, str) and name in RECEIVER_HEALTH_TARGETS:
                poll_unhealthy.add(name)
            else:
                unknown_poll_failure_count += 1
    raw_unknown_poll_count = observation.get("unknown_poll_failure_count")
    if type(raw_unknown_poll_count) is int and raw_unknown_poll_count >= 0:
        unknown_poll_failure_count = max(
            unknown_poll_failure_count, min(1000, raw_unknown_poll_count)
        )

    raw_targets = observation.get("targets")
    targets: dict[str, dict[str, object]] = {}
    unknown_target_count = 0
    if isinstance(raw_targets, dict):
        for name, raw_target in raw_targets.items():
            if not isinstance(name, str) or name not in RECEIVER_HEALTH_TARGETS:
                unknown_target_count += 1
                continue
            if not isinstance(raw_target, dict):
                raw_target = {}
            raw_error = raw_target.get("follow_error_category")
            if raw_error is None:
                error_category = None
            elif isinstance(raw_error, str) and raw_error in RECEIVER_COMPONENT_ERROR_CATEGORIES:
                error_category = raw_error
            else:
                error_category = "Other"
            raw_elapsed = raw_target.get("elapsed_ms")
            elapsed_ms = (
                min(30_000, raw_elapsed) if type(raw_elapsed) is int and raw_elapsed >= 0 else None
            )
            targets[name] = {
                "initial_status": bounded_http_status(raw_target.get("initial_status")),
                "redirect": raw_target.get("redirect") is True,
                "redirect_to_ops_host": raw_target.get("redirect_to_ops_host") is True,
                "final_status": bounded_http_status(raw_target.get("final_status")),
                "final_host_is_ops": raw_target.get("final_host_is_ops") is True,
                "follow_error_category": error_category,
                "elapsed_ms": elapsed_ms,
            }

    return {
        "poll_attempts": poll_attempts,
        "poll_elapsed_ms": poll_elapsed_ms,
        "poll_budget_exceeded": poll_budget_exceeded,
        "poll_healthy": (
            observation.get("poll_healthy") is True
            and poll_budget_exceeded is False
            and type(poll_elapsed_ms) is int
            and poll_elapsed_ms <= 60_000
        ),
        "poll_unhealthy": sorted(poll_unhealthy),
        "unknown_poll_failure_count": unknown_poll_failure_count,
        "receiver_health": health,
        "targets": targets,
        "target_count": len(targets),
        "unknown_target_count": unknown_target_count,
    }


def bounded_http_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def safe_ops_probe_failure_summary(
    exit_code: int, stdout: str | None, stderr: str | None
) -> dict[str, object]:
    safe_stdout = stdout or ""
    safe_stderr = stderr or ""
    output = (safe_stdout + "\n" + safe_stderr).lower()
    categories: set[str] = set()
    patterns = {
        "certificate_validation": ("certificate verify failed", "sslcertverificationerror"),
        "compose_service_missing": ("no such service",),
        "connection_refused": ("connectionrefusederror", "connection refused"),
        "container_not_running": ("is not running", "container is stopped"),
        "missing_file": ("filenotfounderror", "no such file or directory"),
        "mount_configuration": ("invalid mount config",),
        "permission_denied": ("permissionerror", "permission denied"),
        "timeout": ("timeouterror", "timed out", "timeout"),
        "tls_error": ("sslerror", "tlsv1 alert"),
        "transport_error": ("httpexception", "connection reset", "broken pipe"),
    }
    for category, needles in patterns.items():
        if any(needle in output for needle in needles):
            categories.add(category)
    if "traceback (most recent call last)" in output:
        categories.add("python_traceback")
    if not categories and (safe_stdout or safe_stderr):
        categories.add("unclassified_failure")
    return {
        "exit_code": exit_code,
        "stdout_bytes": len(safe_stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(safe_stderr.encode("utf-8", errors="replace")),
        "probe_result_marker_present": "task6_probe:" in output,
        "failure_categories": sorted(categories),
    }


def safe_ops_probe_result_summary(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        return {"available": False}

    observations: dict[str, dict[str, object]] = {}
    raw_observations = result.get("stage_observations")
    if isinstance(raw_observations, dict):
        for stage in OPS_PROBE_STAGES:
            raw = raw_observations.get(stage)
            if not isinstance(raw, dict):
                continue
            observation: dict[str, object] = {}
            status = raw.get("http_status")
            if type(status) is int and 100 <= status <= 599:
                observation["http_status"] = status
            kind = raw.get("json_kind")
            if isinstance(kind, str) and kind in OPS_PROBE_RESPONSE_KINDS:
                observation["json_kind"] = kind
            count = raw.get("list_count")
            if type(count) is int and 0 <= count <= 1000:
                observation["list_count"] = count
            category = raw.get("failure_category")
            if isinstance(category, str) and category in OPS_PROBE_FAILURE_CATEGORIES:
                observation["failure_category"] = category
            observations[stage] = observation

    summary: dict[str, object] = {"available": True}
    for name in (
        "health",
        "unauth_org",
        "admin_org",
        "datasource_status",
        "dashboard_status",
        "ops_business_status",
        "ops_health_status",
        "spoofed_forwarded_status",
    ):
        value = result.get(name)
        summary[name] = value if type(value) is int and 100 <= value <= 599 else None

    raw_uids = result.get("datasource_uids")
    uids = (
        sorted(uid for uid in raw_uids if isinstance(uid, str) and uid in OPS_PROBE_DATASOURCE_UIDS)
        if isinstance(raw_uids, list)
        else []
    )
    summary["datasource_uids"] = uids
    summary["trace_link_config_ok"] = result.get("trace_link_config_ok") is True
    summary["tempo_trace_filter_config_ok"] = result.get("tempo_trace_filter_config_ok") is True
    raw_dashboard_uids = result.get("dashboard_uids")
    summary["dashboard_uids"] = (
        sorted(
            uid
            for uid in raw_dashboard_uids
            if isinstance(uid, str) and uid in OPS_PROBE_DASHBOARD_UIDS
        )
        if isinstance(raw_dashboard_uids, list)
        else []
    )
    for field in ("datasource_unknown_uid_count", "datasource_malformed_item_count"):
        value = result.get(field)
        summary[field] = value if type(value) is int and 0 <= value <= 1000 else None

    last_stage = result.get("last_stage")
    summary["last_stage"] = (
        last_stage if isinstance(last_stage, str) and last_stage in OPS_PROBE_STAGES else None
    )
    tls_category = result.get("tls_exception_category")
    summary["tls_exception_category"] = (
        tls_category
        if isinstance(tls_category, str) and tls_category in {"certificate_validation", "tls_error"}
        else None
    )
    raw_categories = result.get("failure_categories")
    summary["failure_categories"] = (
        sorted(
            {
                item
                for item in raw_categories
                if isinstance(item, str) and item in OPS_PROBE_FAILURE_CATEGORIES
            }
        )
        if isinstance(raw_categories, list)
        else []
    )
    summary["stage_observations"] = observations
    return summary


def summarize_loki_signal(
    http_status: int | None,
    body: object,
    *,
    request_id: str,
    trace_id: str,
    expected_release: str,
) -> dict[str, object]:
    response = body if isinstance(body, dict) else {}
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    results = data.get("result") if isinstance(data.get("result"), list) else []
    streams = results[:100]
    rows: list[tuple[dict[str, object], dict[str, object], int | None]] = []
    stream_label_keys: set[str] = set()
    field_keys: set[str] = set()
    finite_labels: set[tuple[str, str]] = set()
    entry_count = 0
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        stream_label_keys.update(str(key) for key in labels)
        service = labels.get("service_name")
        environment = labels.get("deployment_environment_name")
        if service in {"api", "gateway", "worker"} and environment in {
            "demo",
            "development",
            "production",
        }:
            finite_labels.add((str(service), str(environment)))
        values = stream.get("values") if isinstance(stream.get("values"), list) else []
        for entry in values[:100]:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            entry_count += 1
            event_timestamp_ns = None
            raw_timestamp = entry[0]
            if type(raw_timestamp) in (int, str):
                try:
                    parsed_timestamp = int(raw_timestamp)
                except (TypeError, ValueError, OverflowError):
                    parsed_timestamp = 0
                if 0 < parsed_timestamp < 100_000_000_000_000_000_000:
                    event_timestamp_ns = parsed_timestamp
            try:
                message = json.loads(entry[1]) if isinstance(entry[1], str) else {}
            except (json.JSONDecodeError, TypeError):
                message = {}
            fields = message if isinstance(message, dict) else {}
            metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            combined = {**fields, **metadata}
            field_keys.update(str(key) for key in combined)
            rows.append((labels, combined, event_timestamp_ns))

    request_match = any(fields.get("request_id") == request_id for _, fields, _ in rows)
    trace_match = any(fields.get("trace_id") == trace_id for _, fields, _ in rows)
    event_match = any(fields.get("event") == "gateway.request" for _, fields, _ in rows)
    release_match = any(fields.get("release") == expected_release for _, fields, _ in rows)
    trace_format = any(
        re.fullmatch(r"[a-f0-9]{32}", str(fields.get("trace_id", ""))) for _, fields, _ in rows
    )
    finite_resource_identity_ok = any(
        labels.get("service_name") == "gateway"
        and labels.get("deployment_environment_name") == "demo"
        for labels, _, _ in rows
    )
    matching_request_timestamps = [
        timestamp
        for _, fields, timestamp in rows
        if fields.get("request_id") == request_id
        and fields.get("event") == "gateway.request"
        and timestamp is not None
    ]
    if http_status != 200:
        failure = "http_status"
    elif response.get("status") != "success":
        failure = "api_status"
    elif len(results) != 1 or entry_count != 1:
        failure = "event_cardinality"
    elif not finite_resource_identity_ok:
        failure = "finite_resource_identity_missing"
    elif not (request_match and event_match):
        failure = "request_event_missing"
    elif not (trace_match and trace_format):
        failure = "trace_correlation_missing"
    elif not release_match:
        failure = "release_missing"
    else:
        failure = None
    return {
        "http_status": http_status,
        "body_status_ok": response.get("status") == "success",
        "stream_count": len(results),
        "entry_count": entry_count,
        "query_result_stream_keys": sorted(stream_label_keys)[:40],
        "query_result_resource_identity": [
            {"service_name": service, "deployment_environment_name": environment}
            for service, environment in sorted(finite_labels)
        ],
        "structured_field_keys": sorted(field_keys)[:40],
        "request_id_match": request_match,
        "trace_id_match": trace_match,
        "trace_id_format_ok": trace_format,
        "event_match": event_match,
        "release_match": release_match,
        "request_event_timestamp_unix_ns": (
            matching_request_timestamps[0] if len(matching_request_timestamps) == 1 else None
        ),
        "failure_category": failure,
    }


def summarize_loki_native_application_log(
    http_status: int | None,
    body: object,
    *,
    expected_service: str,
    expected_event: str,
    expected_release: str,
) -> dict[str, object]:
    allowed_events = {
        "api": {"http.server.request"},
        "worker": {"worker.heartbeat", "worker.scan", "worker.candidate"},
    }
    if (
        expected_service not in allowed_events
        or expected_event not in allowed_events.get(expected_service, set())
        or not re.fullmatch(r"[a-f0-9]{40}", expected_release)
    ):
        return {
            "http_status": http_status,
            "body_status_ok": False,
            "stream_count": 0,
            "entry_count": 0,
            "service_name": None,
            "deployment_environment_name": None,
            "event_match": False,
            "release_match": False,
            "failure_category": "invalid_expectation",
        }

    response = body if isinstance(body, dict) else {}
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    results = data.get("result") if isinstance(data.get("result"), list) else []
    stream_keys: set[str] = set()
    field_keys: set[str] = set()
    entries: list[tuple[dict[str, object], str, dict[str, object], dict[str, object]]] = []
    for stream in results[:100]:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        stream_keys.update(str(key) for key in labels)
        values = stream.get("values") if isinstance(stream.get("values"), list) else []
        for entry in values[:100]:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            line = entry[1] if isinstance(entry[1], str) else ""
            try:
                parsed = json.loads(line) if line else {}
            except json.JSONDecodeError:
                parsed = {}
            body_fields = parsed if isinstance(parsed, dict) else {}
            raw_metadata = entry[2] if len(entry) > 2 else {}
            if isinstance(raw_metadata, dict):
                metadata = raw_metadata
            elif isinstance(raw_metadata, list):
                metadata = {
                    item.get("key"): item.get("value")
                    for item in raw_metadata[:100]
                    if isinstance(item, dict) and isinstance(item.get("key"), str)
                }
            else:
                metadata = {}
            field_keys.update(str(key) for key in body_fields)
            field_keys.update(str(key) for key in metadata)
            entries.append((labels, line, body_fields, metadata))

    def event_matches(line: str, body_fields: dict[str, object], metadata: dict[str, object]):
        if line == expected_event:
            return True
        return any(
            source.get(key) == expected_event
            for source in (body_fields, metadata)
            for key in ("event", "event_name", "event.name")
        )

    def release_matches(labels: dict[str, object], metadata: dict[str, object]) -> bool:
        release_keys = ("service_version", "service.version", "release")
        return any(
            source.get(key) == expected_release
            for source in (labels, metadata)
            for key in release_keys
        )

    identity_rows = [
        row
        for row in entries
        if row[0].get("service_name") == expected_service
        and row[0].get("deployment_environment_name") == "demo"
    ]
    event_rows = [row for row in identity_rows if event_matches(row[1], row[2], row[3])]
    release_rows = [row for row in identity_rows if release_matches(row[0], row[3])]
    matching_rows = [row for row in event_rows if release_matches(row[0], row[3])]

    if http_status != 200:
        failure = "http_status"
    elif response.get("status") != "success":
        failure = "api_status"
    elif not results:
        failure = "empty_results"
    elif not entries:
        failure = "empty_entries"
    elif not identity_rows:
        failure = "resource_identity"
    elif not event_rows:
        failure = "event_missing"
    elif not release_rows:
        failure = "release_missing"
    elif not matching_rows:
        failure = "event_release_mismatch"
    else:
        failure = None

    return {
        "http_status": http_status,
        "body_status_ok": response.get("status") == "success",
        "stream_count": min(100, len(results)),
        "entry_count": len(entries),
        "query_result_stream_keys": sorted(stream_keys)[:40],
        "structured_field_keys": sorted(field_keys)[:40],
        "service_name": expected_service if identity_rows else None,
        "deployment_environment_name": "demo" if identity_rows else None,
        "event_match": bool(event_rows),
        "release_match": bool(release_rows),
        "matching_record": bool(matching_rows),
        "failure_category": failure,
    }


def summarize_api_readiness_observation(observation: object) -> dict[str, object]:
    value = observation if isinstance(observation, dict) else {}
    raw_status = value.get("http_status")
    status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else None
    body = value.get("body") if isinstance(value.get("body"), dict) else None
    body_status = "ready" if body is not None and body.get("status") == "ready" else "other"
    error = value.get("error_category")
    error_category = (
        error
        if isinstance(error, str)
        and error in {"TransportError", "SubprocessError", "InvalidEnvelope"}
        else None
    )
    json_valid = value.get("json_valid") is True
    truncated = value.get("truncated") is True
    passed = (
        status == 200
        and body_status == "ready"
        and json_valid
        and value.get("truncated") is False
        and value.get("error_category") is None
    )
    return {
        "http_status": status,
        "json_valid": json_valid,
        "body_status": body_status,
        "truncated": truncated,
        "error_category": error_category,
        "passed": passed,
    }


def summarize_loki_index_series(
    http_status: int | None,
    body: object,
    *,
    expected_request_id: str,
) -> dict[str, object]:
    response = body if isinstance(body, dict) else {}
    series = response.get("data") if isinstance(response.get("data"), list) else []
    bounded_series = series[:1000]
    expected_keys = {"service_name", "deployment_environment_name"}
    key_sets: set[tuple[str, ...]] = set()
    identities: set[tuple[str, str]] = set()
    request_id_indexed = False
    schema_ok = bool(series) and len(series) <= 1000
    for labelset in bounded_series:
        if not isinstance(labelset, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labelset.items()
        ):
            schema_ok = False
            continue
        keys = set(labelset)
        key_sets.add(tuple(sorted(keys)))
        service = labelset.get("service_name")
        environment = labelset.get("deployment_environment_name")
        if service in {
            "api",
            "gateway",
            "worker",
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
        } and environment in {"demo", "local", "production", "development", "unknown"}:
            identities.add((str(service), str(environment)))
        if "request_id" in labelset or expected_request_id in labelset.values():
            request_id_indexed = True
        if keys != expected_keys or service != "gateway" or environment != "demo":
            schema_ok = False

    if http_status != 200:
        failure = "http_status"
    elif response.get("status") != "success":
        failure = "api_status"
    elif not isinstance(response.get("data"), list):
        failure = "invalid_response_shape"
    elif not series:
        failure = "empty_series"
    elif len(series) > 1000:
        failure = "series_count_limit"
    elif not schema_ok:
        failure = "index_schema"
    else:
        failure = None
    return {
        "http_status": http_status,
        "body_status_ok": response.get("status") == "success",
        "series_count": len(series),
        "index_label_keys": sorted({key for labels in key_sets for key in labels})[:40],
        "index_label_schemas": [list(labels) for labels in sorted(key_sets)[:16]],
        "index_resource_identity": [
            {"service_name": service, "deployment_environment_name": environment}
            for service, environment in sorted(identities)
        ],
        "index_schema_ok": schema_ok,
        "request_id_indexed": request_id_indexed,
        "failure_category": failure,
    }


def _tempo_trace_fields(value: object) -> tuple[list[str], dict[str, set[str]]]:
    trace_ids: list[str] = []
    resources: dict[str, set[str]] = {
        "service.name": set(),
        "deployment.environment.name": set(),
        "service.version": set(),
    }
    queue: list[tuple[object, int, bool]] = [(value, 0, False)]
    visited = 0
    while queue and visited < 10000:
        node, depth, is_span = queue.pop()
        visited += 1
        if depth > 16:
            continue
        if isinstance(node, dict):
            if is_span:
                for key in ("traceId", "traceID", "trace_id"):
                    item = node.get(key)
                    if isinstance(item, str):
                        trace_ids.append(item)
            for key, item in node.items():
                if key == "key" and item in resources:
                    raw_value = node.get("value")
                    if isinstance(raw_value, dict):
                        raw_value = raw_value.get("stringValue")
                    if isinstance(raw_value, str):
                        resources[item].add(raw_value)
                if isinstance(item, dict):
                    queue.append((item, depth + 1, False))
                elif isinstance(item, list):
                    in_span_list = key == "spans"
                    queue.extend(
                        (child, depth + 1, in_span_list)
                        for child in item[:1000]
                        if isinstance(child, (dict, list))
                    )
        elif isinstance(node, list):
            queue.extend(
                (item, depth + 1, is_span) for item in node[:1000] if isinstance(item, (dict, list))
            )
    finite_resources = {
        "service.name": {
            item for item in resources["service.name"] if item in {"api", "gateway", "worker"}
        },
        "deployment.environment.name": {
            item
            for item in resources["deployment.environment.name"]
            if item in {"demo", "development", "production"}
        },
        "service.version": resources["service.version"],
    }
    return trace_ids, finite_resources


def summarize_tempo_signal(
    http_status: int | None,
    body: object,
    expected_trace_id: str,
    *,
    expected_release: str | None = None,
) -> dict[str, object]:
    import base64

    response = body if isinstance(body, dict) else {}
    trace_ids, resources = _tempo_trace_fields(body)
    expected_hex = expected_trace_id.lower()
    try:
        expected_base64 = base64.b64encode(bytes.fromhex(expected_trace_id)).decode("ascii")
    except ValueError:
        expected_base64 = ""
    hex_match = any(item.lower() == expected_hex for item in trace_ids)
    base64_match = any(item == expected_base64 for item in trace_ids)
    if http_status != 200:
        failure = "http_status"
    elif not isinstance(body, dict):
        failure = "invalid_json_shape"
    elif not trace_ids:
        failure = "trace_id_field_missing"
    elif not (hex_match or base64_match):
        failure = "trace_id_not_found"
    else:
        failure = None
    return {
        "http_status": http_status,
        "top_level_keys": sorted(str(key) for key in response)[:40],
        "trace_id_field_count": len(trace_ids),
        "trace_id_hex_match": hex_match,
        "trace_id_base64_match": base64_match,
        "resource_services": sorted(resources["service.name"]),
        "resource_environments": sorted(resources["deployment.environment.name"]),
        "resource_release_match": (
            expected_release in resources["service.version"] if expected_release else None
        ),
        "failure_category": failure,
    }


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OBSERVABILITY_TESTS") != "1",
    reason="opt-in Docker observability acceptance",
)


def invoke(args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _prometheus_rule_test_command() -> list[str]:
    lock = json.loads((ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig"))
    rules = ROOT / "tools/infra/prometheus-rules.yml"
    rule_tests = ROOT / "tools/infra/prometheus-rule-tests.yml"
    rules_target = "/etc/prometheus/rules/prometheus-rules.yml"
    tests_target = "/etc/prometheus/rules/prometheus-rule-tests.yml"
    return [
        "docker",
        "run",
        "--pull=missing",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        "1.0",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
        "--workdir",
        "/etc/prometheus/rules",
        "--mount",
        f"type=bind,source={rules.resolve()},target={rules_target},readonly",
        "--mount",
        f"type=bind,source={rule_tests.resolve()},target={tests_target},readonly",
        "--entrypoint",
        "/bin/promtool",
        lock["images"]["prometheus"],
        "test",
        "rules",
        tests_target,
    ]


def _run_prometheus_rule_tests() -> subprocess.CompletedProcess[str]:
    return invoke(_prometheus_rule_test_command(), timeout=120)


def test_prometheus_rule_fixture_with_locked_promtool() -> None:
    result = _run_prometheus_rule_tests()
    assert result.returncode == 0, (
        "The pinned Prometheus rule fixture failed."
        f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def make_certificate(tmp_path: Path, hostname: str = "app.example.invalid") -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    filename = re.sub(r"[^A-Za-z0-9_-]", "_", hostname)
    cert_path = tmp_path / f"{filename}.crt"
    key_path = tmp_path / f"{filename}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _assert_test_subnet_is_unused() -> None:
    listed = invoke(["docker", "network", "ls", "-q"])
    assert listed.returncode == 0, "Docker network inventory was unavailable"
    for network_id in listed.stdout.splitlines():
        result = invoke(
            [
                "docker",
                "network",
                "inspect",
                "--format",
                "{{range .IPAM.Config}}{{println .Subnet}}{{end}}",
                network_id,
            ]
        )
        assert result.returncode == 0, "Docker network subnet inventory was incomplete"
        for value in result.stdout.splitlines():
            if not value:
                continue
            assert not GATEWAY_SUBNET.overlaps(ipaddress.ip_network(value)), (
                "the fixed gateway fixture subnet overlaps an existing Docker network"
            )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _LocalTLSConnection(http.client.HTTPSConnection):
    """Connect to loopback while preserving the requested TLS verification name."""

    def connect(self) -> None:
        raw_socket = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _https_probe(port: int, cert: Path) -> tuple[int, bytes]:
    context = ssl.create_default_context(cafile=str(cert))
    connection = _LocalTLSConnection("app.example.invalid", port, timeout=2, context=context)
    try:
        connection.request("GET", "/health/ready", headers={"Host": "app.example.invalid"})
        response = connection.getresponse()
        return response.status, response.read(4096)
    finally:
        connection.close()


def _wait_https_ready(port: int, cert: Path, timeout: float = 15) -> tuple[int, bytes] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = _https_probe(port, cert)
            if status == 200:
                return status, body
        except (OSError, ssl.SSLError, http.client.HTTPException):
            pass
        time.sleep(0.2)
    return None


def _inspect_owned_container(name: str, token: str) -> dict[str, object] | None:
    inspected = invoke(["docker", "inspect", name])
    if inspected.returncode:
        return None
    records = json.loads(inspected.stdout)
    if len(records) != 1:
        return None
    record = records[0]
    labels = record.get("Config", {}).get("Labels") or {}
    if record.get("Name") != "/" + name or labels.get(OWNER_LABEL) != token:
        return None
    return record


def _remove_owned_container(name: str, token: str) -> None:
    record = _inspect_owned_container(name, token)
    if record is None:
        return
    if record.get("State", {}).get("Running"):
        invoke(["docker", "stop", "--timeout", "0", name])
    invoke(["docker", "rm", name])


def _start_gateway(
    *,
    name: str,
    token: str,
    network: str,
    port: int,
    certificate: Path,
    key: Path,
    entrypoint: Path,
    ops_cidrs: str = "192.0.2.5/32",
) -> subprocess.CompletedProcess[str]:
    deploy_nginx = ROOT / "tools/deploy/nginx"
    return invoke(
        [
            "docker",
            "run",
            "--pull=never",
            "--detach",
            "--name",
            name,
            "--label",
            f"{OWNER_LABEL}={token}",
            "--network",
            network,
            "--ip",
            GATEWAY_IP,
            "--user",
            "101:101",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
            "--tmpfs",
            "/var/log/prl:rw,nosuid,nodev,noexec,size=2m,uid=101,gid=101,mode=1770",
            "--publish",
            f"127.0.0.1:{port}:8443",
            "-e",
            "PRL_GATEWAY_ENVIRONMENT=demo",
            "-e",
            "SERVICE_RELEASE=" + "a" * 40,
            "-e",
            "PRL_OPS_HOST=ops.example.invalid",
            "-e",
            f"PRL_OPS_CIDRS={ops_cidrs}",
            "-v",
            f"{deploy_nginx / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
            "-v",
            f"{deploy_nginx}:/etc/nginx/prl:ro",
            "-v",
            f"{deploy_nginx / 'https.conf'}:/etc/nginx/server.conf:ro",
            "-v",
            f"{entrypoint}:/usr/local/sbin/prl-gateway-entrypoint:ro",
            "-v",
            f"{certificate}:/etc/nginx/tls/server.crt:ro",
            "-v",
            f"{key}:/etc/nginx/tls/server.key:ro",
            "-v",
            f"{certificate}:/etc/nginx/tls/ops.crt:ro",
            "-v",
            f"{key}:/etc/nginx/tls/ops.key:ro",
            NGINX_IMAGE,
            "/bin/sh",
            "/usr/local/sbin/prl-gateway-entrypoint",
        ],
        timeout=45,
    )


def _wait_container_exit(name: str, token: str, timeout: float = 15) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = _inspect_owned_container(name, token)
        if record is None:
            return None
        state = record.get("State", {})
        if state.get("Status") in {"exited", "dead"}:
            return int(state.get("ExitCode", -1))
        time.sleep(0.2)
    return None


def test_gateway_readiness_requires_real_https_response_without_grafana_dns(
    tmp_path: Path,
) -> None:
    """Prove app traffic works, and prove the old literal Grafana proxy blocks startup."""
    token = uuid4().hex
    prefix = f"prl-task6-opsdns-{token}"
    network = prefix
    api_name = f"{prefix}-api"
    gateway_name = f"{prefix}-gateway"
    old_gateway_name = f"{prefix}-literal"
    invalid_gateway_name = f"{prefix}-invalid-cidr"
    owner = f"{OWNER_LABEL}={token}"
    cert, key = make_certificate(tmp_path)
    entrypoint = ROOT / "tools/deploy/nginx/gateway-entrypoint.sh"
    original = entrypoint.read_text(encoding="utf-8")
    dynamic_lines = (
        "        set $prl_ops_upstream http://grafana:3000;\n        proxy_pass $prl_ops_upstream;"
    )
    assert original.count(dynamic_lines) == 1, "the dynamic Grafana proxy seam changed"
    old_literal = tmp_path / "old-literal-entrypoint.sh"
    old_literal.write_bytes(
        original.replace(
            dynamic_lines,
            "        proxy_pass http://grafana:3000;",
            1,
        ).encode("utf-8")
    )
    api_config = tmp_path / "api-fixture.conf"
    api_config.write_text(
        "pid /tmp/nginx.pid;\n"
        "error_log /dev/stderr emerg;\n"
        "events { worker_connections 32; }\n"
        "http { server { listen 8000; default_type application/json; "
        'location = /health/ready { return 200 \'{"status":"ready"}\'; } } }\n',
        encoding="utf-8",
    )
    ids = invoke(["docker", "ps", "-aq", "--filter", f"name=^{api_name}$"])
    assert ids.returncode == 0 and not ids.stdout.strip()
    assert invoke(["docker", "image", "inspect", NGINX_IMAGE]).returncode == 0
    _assert_test_subnet_is_unused()

    network_created = False
    try:
        created = invoke(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--subnet",
                str(GATEWAY_SUBNET),
                "--label",
                owner,
                network,
            ]
        )
        assert created.returncode == 0, "the exact UUID-owned gateway fixture network failed"
        network_created = True

        api_started = invoke(
            [
                "docker",
                "run",
                "--pull=never",
                "--detach",
                "--name",
                api_name,
                "--label",
                owner,
                "--network",
                network,
                "--ip",
                GATEWAY_API_IP,
                "--user",
                "101:101",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "64m",
                "--memory-swap",
                "64m",
                "--cpus",
                "0.25",
                "--pids-limit",
                "32",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=4m,uid=101,gid=101,mode=0700",
                "--tmpfs",
                "/var/cache/nginx:rw,nosuid,nodev,noexec,size=4m,uid=101,gid=101,mode=0700",
                "-v",
                f"{api_config}:/etc/nginx/nginx.conf:ro",
                "--entrypoint",
                "nginx",
                NGINX_IMAGE,
                "-g",
                "daemon off;",
            ]
        )
        assert api_started.returncode == 0, "the bounded fixture API did not start"

        good_port = _free_loopback_port()
        good_started = _start_gateway(
            name=gateway_name,
            token=token,
            network=network,
            port=good_port,
            certificate=cert,
            key=key,
            entrypoint=entrypoint,
            ops_cidrs=fullstack_ops_peer_cidrs(),
        )
        assert good_started.returncode == 0, "the pinned gateway fixture did not launch"
        ready = _wait_https_ready(good_port, cert)
        if ready != (200, b'{"status":"ready"}'):
            gateway_state = _inspect_owned_container(gateway_name, token)
            api_state = _inspect_owned_container(api_name, token)
            gateway_logs = invoke(["docker", "logs", gateway_name])
            api_logs = invoke(["docker", "logs", api_name])
            pytest.fail(
                "verified HTTPS application readiness did not succeed through the gateway; "
                f"probe={ready!r}, gateway_state="
                f"{(gateway_state or {}).get('State')!r}, api_state="
                f"{(api_state or {}).get('State')!r}, "
                f"gateway_log_tail={gateway_logs.stdout[-2000:]!r}, "
                f"api_log_tail={api_logs.stdout[-2000:]!r}, "
                f"api_stderr_tail={api_logs.stderr[-2000:]!r}"
            )
        _remove_owned_container(gateway_name, token)

        invalid_started = _start_gateway(
            name=invalid_gateway_name,
            token=token,
            network=network,
            port=_free_loopback_port(),
            certificate=cert,
            key=key,
            entrypoint=entrypoint,
            ops_cidrs="192.0.2.5/32,198.51.100.0/24/32",
        )
        assert invalid_started.returncode == 0, "the malformed CIDR control did not launch"
        invalid_exit = _wait_container_exit(invalid_gateway_name, token)
        assert invalid_exit not in (None, 0), "an individual CIDR with two slashes was accepted"
        invalid_logs = invoke(["docker", "logs", invalid_gateway_name])
        assert invalid_logs.returncode == 0 and "gateway operations allowlist rejected" in (
            invalid_logs.stdout + invalid_logs.stderr
        ), "the malformed individual CIDR did not fail the gateway allowlist check"
        _remove_owned_container(invalid_gateway_name, token)

        old_port = _free_loopback_port()
        old_started = _start_gateway(
            name=old_gateway_name,
            token=token,
            network=network,
            port=old_port,
            certificate=cert,
            key=key,
            entrypoint=old_literal,
        )
        assert old_started.returncode == 0, "the old-literal control fixture did not launch"
        old_exit = _wait_container_exit(old_gateway_name, token)
        assert old_exit not in (
            None,
            0,
        ), "the negative control did not detect the literal Grafana DNS dependency"
        old_logs = invoke(["docker", "logs", old_gateway_name])
        assert (
            old_logs.returncode == 0 and "grafana" in (old_logs.stdout + old_logs.stderr).lower()
        ), "the literal proxy control did not fail while resolving Grafana"
    finally:
        for name in (old_gateway_name, invalid_gateway_name, gateway_name, api_name):
            _remove_owned_container(name, token)
        if network_created:
            inspected = invoke(
                [
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    '{{index .Labels "org.prl.task6.owner"}}|{{.Name}}',
                    network,
                ]
            )
            if inspected.returncode == 0 and inspected.stdout.strip() == f"{token}|{network}":
                invoke(["docker", "network", "rm", network])


FULL_STACK_FLAGS = (
    "RUN_CONTAINER_TESTS",
    "RUN_COMPOSE_TESTS",
    "RUN_GATEWAY_TESTS",
    "RUN_COMPOSE_ACCEPTANCE",
    "RUN_OBSERVABILITY_TESTS",
)


def _full_stack_enabled() -> bool:
    return all(os.environ.get(name) == "1" for name in FULL_STACK_FLAGS)


def _https_request(
    hostname: str, port: int, certificate: Path, method: str, path: str
) -> tuple[int, dict[str, str], bytes]:
    context = ssl.create_default_context(cafile=str(certificate))
    connection = _LocalTLSConnection(hostname, port, timeout=4, context=context)
    try:
        connection.request(method, path, headers={"Host": hostname, "Connection": "close"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read(1_000_000)
    finally:
        connection.close()


class FullStackRuntime:
    """One bounded UUID-owned Compose run covering the Task 6 wire-up."""

    def __init__(
        self,
        scratch: Path,
        *,
        use_acceptance_token: bool = True,
        pin_ops_edge_network: bool = False,
    ) -> None:
        self.app_host = "app.example.invalid"
        self.stack = ComposeAcceptance(scratch, origin_scheme="https", origin_host=self.app_host)
        additional_subnets = (OPS_EDGE_TEST_SUBNET,) if pin_ops_edge_network else ()
        token = os.environ.get("PRL_TASK6_ACCEPTANCE_TOKEN", "") if use_acceptance_token else ""
        if token:
            if not re.fullmatch(r"[0-9a-f]{32}", token):
                raise AcceptanceFailure("The runtime acceptance token is invalid")
            self.stack.project = "prl-task6-" + token
            self.stack.image_tag = "task6-" + token[:20]
            self.stack.env["PRL_IMAGE_TAG"] = self.stack.image_tag
            self.stack.volume_name = f"{self.stack.project}_mysql_data"
            self.stack._ensure_unique_ownership()
        if token or pin_ops_edge_network:
            self.stack._ensure_gateway_subnet_available(additional_subnets=additional_subnets)

        self.port = self.stack.port
        self.ops_host = "ops.example.invalid"
        self.app_origin = self.stack.origin
        self.ops_origin = f"https://{self.ops_host}:{self.port}"
        self.last_worker_metric_query_diagnostic: dict[str, object] = {}
        self.app_cert, self.app_key = make_certificate(scratch, self.app_host)
        self.ops_cert, self.ops_key = make_certificate(scratch, self.ops_host)
        self.admin_user = "task6-admin"
        self.admin_password = "synthetic-" + uuid4().hex
        self.admin_user_file = scratch / "grafana-admin-user"
        self.admin_password_file = scratch / "grafana-admin-password"
        self.admin_user_file.write_bytes((self.admin_user + "\n").encode("utf-8"))
        self.admin_password_file.write_bytes((self.admin_password + "\n").encode("utf-8"))

        deploy = ROOT / "tools/deploy"
        self.stack.base = [
            "docker",
            "compose",
            "--project-name",
            self.stack.project,
            "-f",
            str(deploy / "compose.yaml"),
            "-f",
            str(deploy / "compose.https.yaml"),
            "-f",
            str(deploy / "compose.observability.yaml"),
        ]
        if pin_ops_edge_network:
            edge_overlay = scratch / "task8-ops-edge-network.compose.yaml"
            edge_overlay.write_text(fullstack_ops_edge_network_overlay(), encoding="utf-8")
            self.stack.base.extend(["-f", str(edge_overlay.resolve())])
        self.stack.env.update(
            PRL_HTTPS_BIND="127.0.0.1",
            PRL_HTTPS_PORT=str(self.port),
            PRL_HTTPS_ORIGIN=self.app_origin,
            PRL_TLS_CERT_FILE=str(self.app_cert.resolve()),
            PRL_TLS_KEY_FILE=str(self.app_key.resolve()),
            SERVICE_RELEASE=self.stack.expected_sha,
            PRL_OPS_HOST=self.ops_host,
            PRL_OPS_ORIGIN=self.ops_origin,
            PRL_OPS_CIDRS=(fullstack_ops_peer_cidrs() if pin_ops_edge_network else "127.0.0.1/32"),
            PRL_OPS_TLS_CERT_FILE=str(self.ops_cert.resolve()),
            PRL_OPS_TLS_KEY_FILE=str(self.ops_key.resolve()),
            PRL_GRAFANA_ADMIN_USER_FILE=str(self.admin_user_file.resolve()),
            PRL_GRAFANA_ADMIN_PASSWORD_FILE=str(self.admin_password_file.resolve()),
            PRL_EXPECTED_WORKER_SLOTS="1",
            DOCKER_DEFAULT_PLATFORM="linux/amd64",
        )
        self.probe_file = scratch / "task6-probes.compose.yaml"
        self.api_id = ""
        self.target_info_schema: list[list[str]] = []
        self.heartbeat_observations: list[dict[str, object]] = []
        self.observed_worker_id: str | None = None
        self.worker_stream_diagnostic: dict[str, object] | None = None
        self.last_operations_probe: dict[str, object] = {"available": False}
        self.last_receiver_health_summary: dict[str, object] = {"available": False}
        self.last_loki_request_diagnostic: dict[str, object] = {"available": False}
        self.last_loki_index_series_diagnostic: dict[str, object] = {"available": False}

    def wait_app_gateway(self) -> None:
        deadline = time.monotonic() + 90
        last = {
            "gateway": {"status": None, "body_status": None, "error_class": None},
            "api": {"status": None, "body_status": None, "error_class": None},
        }
        while time.monotonic() < deadline:
            for name, path in (
                ("gateway", "/health/gateway"),
                ("api", "/health/ready"),
            ):
                try:
                    status, _headers, body = _https_request(
                        self.app_host, self.port, self.app_cert, "GET", path
                    )
                    try:
                        body_status = json.loads(body).get("status")
                    except (json.JSONDecodeError, AttributeError):
                        body_status = None
                    last[name] = {
                        "status": status,
                        "body_status": body_status,
                        "error_class": None,
                    }
                except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                    last[name] = {
                        "status": None,
                        "body_status": None,
                        "error_class": type(exc).__name__,
                    }
            if last["gateway"]["status"] == 200 and last["api"]["status"] == 200:
                return
            time.sleep(0.5)
        raise AcceptanceFailure(
            "Verified HTTPS gateway/API readiness did not complete: "
            + json.dumps(last, sort_keys=True, separators=(",", ":"))
        )

    def compose(
        self,
        *args: str,
        timeout: float = 180,
        deadline: float | None = None,
    ):
        if deadline is None:
            return self.stack.compose(*args, timeout=timeout)
        if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
            raise AcceptanceFailure("The Compose command deadline is invalid")
        remaining = float(deadline) - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise AcceptanceFailure("The Compose command deadline expired")
        return self.stack.compose(*args, timeout=min(timeout, remaining))

    def api_json(
        self,
        url: str,
        *,
        method: str = "GET",
        body: object | None = None,
        deadline: float | None = None,
    ):
        request_timeout = 5.0
        if deadline is not None:
            if type(deadline) not in (int, float):
                raise AcceptanceFailure("The telemetry API request deadline is invalid")
            try:
                numeric_deadline = float(deadline)
            except (OverflowError, ValueError):
                raise AcceptanceFailure("The telemetry API request deadline is invalid") from None
            if not math.isfinite(numeric_deadline):
                raise AcceptanceFailure("The telemetry API request deadline is invalid")
            remaining = numeric_deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise AcceptanceFailure("The telemetry API request deadline expired")
            request_timeout = min(request_timeout, remaining)
        urlopen_timeout = repr(request_timeout) if deadline is not None else "5"
        script = (
            "import json,sys,urllib.request; "
            "url,method,raw=sys.argv[1:4]; "
            "data=raw.encode() if raw else None; "
            "req=urllib.request.Request(url,data=data,method=method,"
            "headers={'Content-Type':'application/json'} if data else {}); "
            f"res=urllib.request.urlopen(req,timeout={urlopen_timeout}); "
            "print(json.dumps(json.loads(res.read()),separators=(',',':')))"
        )
        command = [
            "docker",
            "exec",
            self.api_id,
            "python",
            "-c",
            script,
            url,
            method,
            json.dumps(body, separators=(",", ":")) if body is not None else "",
        ]
        subprocess_timeout = 15.0
        if deadline is not None:
            remaining = numeric_deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise AcceptanceFailure("The telemetry API request deadline expired")
            subprocess_timeout = min(subprocess_timeout, remaining)
        try:
            result = acceptance_invoke(command, timeout=subprocess_timeout)
        except subprocess.TimeoutExpired:
            if deadline is None:
                raise
            raise AcceptanceFailure(
                "The telemetry API request exceeded its shared deadline"
            ) from None
        if deadline is not None and time.monotonic() >= numeric_deadline:
            raise AcceptanceFailure("The telemetry API request completed after its shared deadline")
        if result.returncode:
            raise AcceptanceFailure("A bounded in-stack telemetry API request failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise AcceptanceFailure("A telemetry API returned invalid JSON") from None

    def api_status(self, url: str, *, method: str, body: object) -> int | None:
        script = (
            "import json,sys,urllib.request,urllib.error\n"
            "url,method,raw=sys.argv[1:4]; data=raw.encode()\n"
            "req=urllib.request.Request(url,data=data,method=method,headers={'Content-Type':'application/json'})\n"
            "status=None\n"
            "try:\n"
            " with urllib.request.urlopen(req,timeout=5) as response:\n"
            "  status=response.status\n"
            "except urllib.error.HTTPError as exc:\n status=exc.code\n"
            "except (OSError,urllib.error.URLError,TimeoutError):\n status=None\n"
            "print(json.dumps({'status':status},separators=(',',':')))"
        )
        result = acceptance_invoke(
            [
                "docker",
                "exec",
                self.api_id,
                "python",
                "-c",
                script,
                url,
                method,
                json.dumps(body, separators=(",", ":")),
            ],
            timeout=15,
        )
        if result.returncode:
            raise AcceptanceFailure("A bounded in-stack status request failed")
        try:
            status = json.loads(result.stdout).get("status")
        except (json.JSONDecodeError, AttributeError):
            raise AcceptanceFailure(
                "A bounded in-stack status request returned invalid data"
            ) from None
        return status if type(status) is int else None

    def loki_scalar(self, expression: str) -> float | None:
        response = self.api_json(
            "http://loki:3100/loki/api/v1/query?" + urlencode({"query": expression})
        )
        if response.get("status") != "success":
            raise AcceptanceFailure("The pinned Loki query API returned a non-success result")
        samples = response.get("data", {}).get("result", [])
        if not isinstance(samples, list):
            raise AcceptanceFailure("The pinned Loki query API returned an invalid vector")
        if not samples:
            return None
        if len(samples) != 1:
            raise AcceptanceFailure("A global Task 7 SLI query returned multiple series")
        value = samples[0].get("value")
        try:
            scalar = float(value[1])
        except (TypeError, ValueError, IndexError):
            raise AcceptanceFailure(
                "A pinned Loki SLI query returned a nonnumeric sample"
            ) from None
        if not math.isfinite(scalar):
            raise AcceptanceFailure("A pinned Loki SLI query returned a nonfinite sample")
        return scalar

    def push_gateway_json_logs(self, entries: list[dict[str, object]]) -> None:
        for offset in range(0, len(entries), 50):
            now = time.time_ns()
            batch = entries[offset : offset + 50]
            values = [
                [str(now + index * 1_000_000), json.dumps(entry, separators=(",", ":"))]
                for index, entry in enumerate(batch)
            ]
            status = self.api_status(
                "http://loki:3100/loki/api/v1/push",
                method="POST",
                body={
                    "streams": [
                        {
                            "stream": {
                                "service_name": "gateway",
                                "deployment_environment_name": "demo",
                            },
                            "values": values,
                        }
                    ]
                },
            )
            if status not in {200, 204}:
                raise AcceptanceFailure(
                    "The pinned Loki push API rejected bounded gateway test events"
                )

    def gateway_sli_truth_table(self) -> dict[str, object]:
        document = yaml.safe_load(
            (ROOT / "tools/infra/loki-rules/fake/gateway-sli.yml").read_text(encoding="utf-8")
        )
        recordings = {
            rule["record"]: rule["expr"]
            for group in document["groups"]
            for rule in group["rules"]
            if "record" in rule
        }
        alert_expressions = {
            rule["alert"]: rule["expr"]
            for group in document["groups"]
            for rule in group["rules"]
            if "alert" in rule
        }
        events: list[dict[str, object]] = []
        for index in range(100):
            events.append(
                {
                    "event": "gateway.request",
                    "classification": "success",
                    "route": "/__task7_alert_fixture",
                    "duration_seconds": 0.25 if index < 85 else 0.75,
                }
            )
        cohort = "/__task7_sli_truth"
        for duration in [0.1] * 16 + [0.500, 0.501]:
            events.append(
                {
                    "event": "gateway.request",
                    "classification": "success",
                    "route": cohort,
                    "duration_seconds": duration,
                }
            )
        for route, count in ((cohort, 2), ("/__task7_all_errors", 2)):
            events.extend(
                {
                    "event": "gateway.request",
                    "classification": "service_failure",
                    "route": route,
                    "duration_seconds": 0.2,
                }
                for _ in range(count)
            )
        for route, count in (("/__task7_all_slow", 20), ("/__task7_sub20", 19)):
            events.extend(
                {
                    "event": "gateway.request",
                    "classification": "success",
                    "route": route,
                    "duration_seconds": 0.75,
                }
                for _ in range(count)
            )
        events.extend(
            {
                "event": "gateway.request",
                "classification": "success",
                "route": "/__task7_recovered",
                "duration_seconds": 0.25,
            }
            for _ in range(20)
        )
        events.append(
            {
                "event": "gateway.request",
                "classification": "out_of_scope",
                "route": "/__task7_excluded_only",
                "duration_seconds": 0.1,
            }
        )
        self.push_gateway_json_logs(events)

        def filter_expression(expression: str, route: str) -> str:
            marker = "| keep event, classification, route"
            if marker not in expression:
                raise AcceptanceFailure("A Task 7 SLI query lost its finite field projection")
            return expression.replace(marker, f'| route="{route}" {marker}')

        def route_query(name: str, route: str) -> float | None:
            return self.loki_scalar(filter_expression(recordings[name], route))

        def route_alert_query(name: str, route: str) -> float | None:
            return self.loki_scalar(filter_expression(alert_expressions[name], route))

        availability = route_query("prl:gateway_availability:ratio5m", cohort)
        fast_success = route_query("prl:gateway_fast_success:ratio5m", cohort)
        all_errors_availability = route_query(
            "prl:gateway_availability:ratio5m", "/__task7_all_errors"
        )
        all_errors_fast = route_query("prl:gateway_fast_success:ratio5m", "/__task7_all_errors")
        excluded_only = route_query("prl:gateway_availability:ratio5m", "/__task7_excluded_only")
        empty = route_query("prl:gateway_availability:ratio5m", "/__task7_empty")
        all_slow_successful_requests = route_query(
            "prl:gateway_successful_requests:sum5m", "/__task7_all_slow"
        )
        all_slow_success_ratio = route_query(
            "prl:gateway_fast_success:ratio5m", "/__task7_all_slow"
        )
        all_slow_alert_predicate = route_alert_query(
            "GatewayFastSuccessBelowSLO", "/__task7_all_slow"
        )
        all_errors_alert_predicate = route_alert_query(
            "GatewayFastSuccessBelowSLO", "/__task7_all_errors"
        )
        sub20_alert_predicate = route_alert_query("GatewayFastSuccessBelowSLO", "/__task7_sub20")
        recovered_alert_predicate = route_alert_query(
            "GatewayFastSuccessBelowSLO", "/__task7_recovered"
        )
        healthcheck_count = self.loki_scalar(recordings["prl:gateway_healthcheck:count1m"])
        fast_alert_predicate = self.loki_scalar(alert_expressions["GatewayFastSuccessBelowSLO"])
        all_slow_alert_present = all_slow_alert_predicate is not None
        sub20_alert_suppressed = sub20_alert_predicate is None
        recovered_alert_suppressed = recovered_alert_predicate is None
        if (
            availability is None
            or not math.isclose(availability, 0.9, rel_tol=1e-6)
            or fast_success is None
            or not math.isclose(fast_success, 17 / 18, rel_tol=1e-6)
            or all_errors_availability is None
            or not math.isclose(all_errors_availability, 0.0, abs_tol=1e-9)
            or all_errors_fast is not None
            or excluded_only is not None
            or empty is not None
            or healthcheck_count is None
            or healthcheck_count < 1
            or fast_alert_predicate is None
            or not 0 < fast_alert_predicate < 0.95
            or all_slow_successful_requests is None
            or not math.isclose(all_slow_successful_requests, 20.0, abs_tol=1e-9)
            or all_slow_success_ratio is None
            or not math.isclose(all_slow_success_ratio, 0.0, abs_tol=1e-9)
            or not all_slow_alert_present
            or not math.isclose(all_slow_alert_predicate, 0.0, abs_tol=1e-9)
            or all_errors_alert_predicate is not None
            or not sub20_alert_suppressed
            or not recovered_alert_suppressed
        ):
            raise AcceptanceFailure("Pinned Loki SLI queries failed a required truth-table case")
        return {
            "availability_success_over_eligible": round(availability, 6),
            "fast_success_inclusive_500ms": round(fast_success, 6),
            "all_errors_availability_zero": round(all_errors_availability, 6),
            "all_errors_fast_success_has_data": all_errors_fast is not None,
            "excluded_only_has_data": excluded_only is not None,
            "empty_eligible_window_has_data": empty is not None,
            "genuine_gateway_healthchecks_in_1m": int(healthcheck_count),
            "fast_success_alert_predicate_value": round(fast_alert_predicate, 6),
            "all_slow_successful_requests": int(all_slow_successful_requests),
            "all_slow_success_ratio": round(all_slow_success_ratio, 6),
            "all_slow_alert_predicate": round(all_slow_alert_predicate, 6),
            "all_slow_alert_present": all_slow_alert_present,
            "all_errors_alert_predicate": all_errors_alert_predicate,
            "sub20_alert_predicate": sub20_alert_predicate,
            "sub20_alert_suppressed": sub20_alert_suppressed,
            "recovered_alert_predicate": recovered_alert_predicate,
            "recovered_alert_suppressed": recovered_alert_suppressed,
            "pushed_gateway_json_events": len(events),
        }

    def alert_receiver_statuses(
        self, name: str, *, deadline: float | None = None
    ) -> list[dict[str, str]]:
        records = self.api_json("http://local_alert_receiver:8088/alerts", deadline=deadline).get(
            "alerts", []
        )
        matching: list[dict[str, str]] = []
        for item in records:
            if not isinstance(item, dict) or item.get("alertname") != name:
                continue
            status = item.get("status")
            severity = item.get("severity")
            received_at = item.get("received_at")
            if not all(isinstance(value, str) for value in (status, severity, received_at)):
                raise AcceptanceFailure("The local receiver returned an invalid rule alert summary")
            fingerprint = item.get("fingerprint")
            starts_at = item.get("starts_at")
            if fingerprint is None and starts_at is None:
                summary = {"status": status, "severity": severity, "received_at": received_at}
            elif (
                not isinstance(fingerprint, str)
                or not re.fullmatch(r"[0-9a-f]{16,64}", fingerprint)
                or not isinstance(starts_at, str)
            ):
                raise AcceptanceFailure("The local receiver returned an invalid alert identity")
            else:
                try:
                    if normalize_alertmanager_timestamp(starts_at) != starts_at:
                        raise ValueError
                except ValueError:
                    raise AcceptanceFailure(
                        "The local receiver returned an invalid alert identity"
                    ) from None
                summary = {
                    "status": status,
                    "severity": severity,
                    "received_at": received_at,
                    "fingerprint": fingerprint,
                    "starts_at": starts_at,
                }
            matching.append(summary)
        if any(
            item["status"] not in {"firing", "resolved"}
            or item["severity"] not in {"critical", "warning", "info"}
            for item in matching
        ):
            raise AcceptanceFailure("The local receiver returned an invalid rule alert summary")
        for item in matching:
            try:
                datetime.fromisoformat(item["received_at"].replace("Z", "+00:00"))
            except ValueError:
                raise AcceptanceFailure(
                    "The local receiver returned an invalid alert receipt timestamp"
                ) from None
        return matching

    def worker_dashboard_metric_inventory(self) -> dict[str, object]:
        names_response = self.api_json("http://prometheus:9090/api/v1/label/__name__/values")
        names = set(names_response.get("data", []))
        expected_worker_gauges = {
            "prl_db_pool_checked_out_ratio",
            "prl_db_pool_idle_ratio",
            "prl_db_pool_overflow_ratio",
            "prl_telemetry_queue_capacity_ratio",
            "prl_telemetry_queue_size_ratio",
        }
        expected_api_gauges = {
            "prl_db_pool_checked_out",
            "prl_db_pool_idle",
            "prl_db_pool_overflow",
            "prl_telemetry_queue_capacity",
            "prl_telemetry_queue_size",
        }
        if not expected_worker_gauges <= names or not expected_api_gauges <= names:
            raise AcceptanceFailure(
                "The actual API/worker gauge names differ from Task 6 exposition"
            )
        verified_worker: dict[str, list[str]] = {}
        for metric_name in sorted(expected_worker_gauges):
            samples = (
                self.api_json(
                    "http://prometheus:9090/api/v1/query?" + urlencode({"query": metric_name})
                )
                .get("data", {})
                .get("result", [])
            )
            matches = [
                sample.get("metric", {})
                for sample in samples
                if sample.get("metric", {}).get("service_name") == "worker"
                and sample.get("metric", {}).get("deployment_environment_name") == "demo"
                and sample.get("metric", {}).get("worker_slot") == "1"
            ]
            if not matches:
                raise AcceptanceFailure(
                    "A worker dashboard gauge lacked its actual finite slot labels"
                )
            verified_worker[metric_name] = sorted(matches[0])
        observed_runtime_counters = sorted(
            name
            for name in (
                "prl_telemetry_exported_total",
                "prl_telemetry_failed_total",
                "prl_telemetry_dropped_total",
            )
            if name in names
        )
        if "prl_telemetry_exported_total" not in observed_runtime_counters:
            raise AcceptanceFailure("The worker runtime export counter was absent from Prometheus")
        collector_loss_families = {
            f"otelcol_{stage}_{kind}"
            for stage in (
                "receiver_refused",
                "receiver_failed",
                "exporter_enqueue_failed",
                "exporter_send_failed",
            )
            for kind in ("log_records", "metric_points", "spans")
        }
        collector_config = yaml.safe_load(
            (ROOT / "tools/infra/otel-collector.yaml").read_text(encoding="utf-8")
        )
        observed_collector_loss_families = []
        collector_loss_counter_sample_labels: dict[str, list[str]] = {}
        collector_loss_counter_source_values: dict[str, list[str]] = {}
        for metric_name in sorted(collector_loss_families & names):
            samples = (
                self.api_json(
                    "http://prometheus:9090/api/v1/query?" + urlencode({"query": metric_name})
                )
                .get("data", {})
                .get("result", [])
            )
            matching = [
                sample.get("metric", {})
                for sample in samples
                if sample.get("metric", {}).get("job") == "collector"
            ]
            if not matching:
                continue
            source_dimension = (
                "receiver" if metric_name.startswith("otelcol_receiver_") else "exporter"
            )
            if any(source_dimension not in labels for labels in matching):
                raise AcceptanceFailure(
                    "An observed Collector loss counter lacked its finite source label"
                )
            if any(
                {"request_id", "run_id", "trace_id", "span_id"} & set(labels) for labels in matching
            ):
                raise AcceptanceFailure(
                    "An observed Collector loss counter included a continuous identity label"
                )
            configured_components = set(
                collector_config["receivers" if source_dimension == "receiver" else "exporters"]
            )
            source_values = sorted({str(labels[source_dimension]) for labels in matching})
            if not set(source_values) <= configured_components:
                raise AcceptanceFailure(
                    "An observed Collector loss source label exceeded configured components"
                )
            observed_collector_loss_families.append(metric_name)
            collector_loss_counter_sample_labels[metric_name] = sorted(
                {key for labels in matching for key in labels}
            )
            collector_loss_counter_source_values[metric_name] = source_values
        absent_collector_loss_families = sorted(
            collector_loss_families - set(observed_collector_loss_families)
        )
        return {
            "worker_gauges": verified_worker,
            "api_direct_gauge_names": sorted(expected_api_gauges),
            "worker_runtime_counter_families_observed": observed_runtime_counters,
            "collector_loss_counter_families_observed": observed_collector_loss_families,
            "collector_loss_counter_families_absent_or_unmaterialized": (
                absent_collector_loss_families
            ),
            "collector_loss_counter_sample_labels": collector_loss_counter_sample_labels,
            "collector_loss_counter_source_values": collector_loss_counter_source_values,
            "collector_loss_counter_absence_semantics": (
                "A missing family can be zero or unmaterialized; it is not proof of zero loss. "
                "Use Collector target health and family presence together."
            ),
        }

    def api_json_observation(self, url: str) -> dict[str, object]:
        script = (
            "import json,sys,urllib.request,urllib.error\n"
            "url=sys.argv[1]; status=None; raw=b''; error=None\n"
            "try:\n"
            " response=urllib.request.urlopen(url,timeout=5)\n"
            " status=response.status; raw=response.read(1048577)\n"
            "except urllib.error.HTTPError as exc:\n"
            " status=exc.code; raw=exc.read(1048577)\n"
            "except (OSError,urllib.error.URLError,TimeoutError):\n"
            " error='TransportError'\n"
            "truncated=len(raw)>1048576; raw=raw[:1048576]\n"
            "try:\n"
            " body=json.loads(raw); valid=True\n"
            "except (ValueError,UnicodeDecodeError):\n"
            " body=None; valid=False\n"
            "print(json.dumps({'http_status':status,'body':body,'json_valid':valid,'truncated':truncated,'error_category':error},separators=(',',':')))"
        )
        try:
            result = acceptance_invoke(
                ["docker", "exec", self.api_id, "python", "-c", script, url], timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "SubprocessError",
            }
        if result.returncode:
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "SubprocessError",
            }
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "InvalidEnvelope",
            }
        if not isinstance(envelope, dict):
            return {
                "http_status": None,
                "body": None,
                "json_valid": False,
                "truncated": False,
                "error_category": "InvalidEnvelope",
            }
        return envelope

    def wait_receiver_health(self) -> dict[str, object]:
        started = time.monotonic()
        deadline = started + 60
        attempts = 0
        while True:
            attempts += 1
            request_started = time.monotonic()
            observation = self.api_json_observation(
                "http://local_alert_receiver:8088/health/telemetry"
            )
            observation["elapsed_ms"] = min(
                30_000, max(0, int((time.monotonic() - request_started) * 1000))
            )
            completed = time.monotonic()
            observation["total_elapsed_ms"] = min(
                120_000, max(0, int((completed - started) * 1000))
            )
            observation["budget_exceeded"] = completed >= deadline
            summary = safe_receiver_health_summary(observation)
            summary["poll_attempts"] = min(120, attempts)
            summary["total_elapsed_ms"] = min(
                60_000, max(0, int((time.monotonic() - started) * 1000))
            )
            self.last_receiver_health_summary = summary
            if (
                receiver_health_is_passed(summary)
                or summary["budget_exceeded"] is True
                or time.monotonic() >= deadline
            ):
                return summary
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def observe_loki_request(
        self,
        request_id: str,
        trace_id: str,
        *,
        start_ns: int | None = None,
    ) -> dict[str, object]:
        query = (
            '{service_name="gateway",deployment_environment_name="demo"} '
            f'| request_id="{request_id}"'
        )
        now_ns = time.time_ns()
        if start_ns is None:
            query_start_ns = now_ns - 600_000_000_000
        elif type(start_ns) is int and 0 < start_ns <= now_ns:
            query_start_ns = start_ns
        else:
            raise AcceptanceFailure("The Loki request query start timestamp is invalid")
        query_end_ns = now_ns + 60_000_000_000
        params = urlencode(
            {
                "query": query,
                "start": str(query_start_ns),
                "end": str(query_end_ns),
                "limit": "20",
            }
        )
        observation = self.api_json_observation(
            "http://loki:3100/loki/api/v1/query_range?" + params
        )
        summary = summarize_loki_signal(
            observation.get("http_status"),
            observation.get("body"),
            request_id=request_id,
            trace_id=trace_id,
            expected_release=self.stack.expected_sha,
        )
        summary["query_start_unix_ns"] = query_start_ns
        summary["query_end_unix_ns"] = query_end_ns
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        self.last_loki_request_diagnostic = summary
        return summary

    def observe_loki_native_application_log(self, service: str, event: str) -> dict[str, object]:
        expected_events = {
            "api": {"http.server.request"},
            "worker": {"worker.heartbeat", "worker.scan", "worker.candidate"},
        }
        if service not in expected_events or event not in expected_events[service]:
            raise AcceptanceFailure("A native Loki log probe used an unsupported source event")
        query = (
            f'{{service_name="{service}",deployment_environment_name="demo"}} '
            f"|= {json.dumps(event)}"
        )
        now_ns = time.time_ns()
        params = urlencode(
            {
                "query": query,
                "start": str(now_ns - 600_000_000_000),
                "end": str(now_ns),
                "limit": "100",
            }
        )
        observation = self.api_json_observation(
            "http://loki:3100/loki/api/v1/query_range?" + params
        )
        summary = summarize_loki_native_application_log(
            observation.get("http_status"),
            observation.get("body"),
            expected_service=service,
            expected_event=event,
            expected_release=self.stack.expected_sha,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        return summary

    def observe_loki_index_series(self, *, expected_request_id: str) -> dict[str, object]:
        now_ns = time.time_ns()
        params = urlencode(
            {
                "match[]": '{service_name="gateway",deployment_environment_name="demo"}',
                "start": str(now_ns - 604_800_000_000_000),
                "end": str(now_ns),
            }
        )
        observation = self.api_json_observation("http://loki:3100/loki/api/v1/series?" + params)
        summary = summarize_loki_index_series(
            observation.get("http_status"),
            observation.get("body"),
            expected_request_id=expected_request_id,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        self.last_loki_index_series_diagnostic = summary
        return summary

    def observe_tempo_trace(self, trace_id: str) -> dict[str, object]:
        observation = self.api_json_observation(f"http://tempo:3200/api/traces/{trace_id}")
        summary = summarize_tempo_signal(
            observation.get("http_status"),
            observation.get("body"),
            trace_id,
            expected_release=self.stack.expected_sha,
        )
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
        return summary

    def _collector_counter_snapshot(self) -> dict[str, object]:
        expected_names = {
            f"otelcol_{stage}_{kind}"
            for stage in (
                "receiver_accepted",
                "receiver_refused",
                "receiver_failed",
                "exporter_sent",
                "exporter_enqueue_failed",
                "exporter_send_failed",
            )
            for kind in ("log_records", "metric_points", "spans")
        }
        selector = (
            '{__name__=~"otelcol_(receiver_(accepted|refused|failed)|'
            'exporter_(sent|enqueue_failed|send_failed))_(log_records|metric_points|spans)"}'
        )
        query = "http://prometheus:9090/api/v1/query?" + urlencode({"query": selector})
        observation = self.api_json_observation(query)
        body = observation.get("body")
        response = body if isinstance(body, dict) else {}
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        series = data.get("result") if isinstance(data.get("result"), list) else []
        grouped: dict[str, list[dict[str, object]]] = {name: [] for name in expected_names}
        finite_values = {
            "receiver": {"otlp", "filelog/gateway"},
            "exporter": {"otlphttp/loki", "otlp/tempo"},
            "data_type": {"logs", "spans"},
        }
        for sample in series[:1000]:
            if not isinstance(sample, dict):
                continue
            labels = sample.get("metric") if isinstance(sample.get("metric"), dict) else {}
            name = labels.get("__name__")
            if name not in grouped:
                continue
            raw_sample = sample.get("value")
            try:
                number = float(raw_sample[1]) if isinstance(raw_sample, (list, tuple)) else 0.0
            except (ValueError, TypeError, IndexError):
                number = 0.0
            safe_dimensions = {
                key: value
                for key, value in labels.items()
                if key in finite_values and value in finite_values[key]
            }
            grouped[name].append(
                {
                    "value": number,
                    "label_keys": sorted(str(key) for key in labels)[:40],
                    "finite_dimensions": safe_dimensions,
                }
            )
        metrics = []
        for name in sorted(expected_names):
            samples = grouped[name]
            dimension_rows = {
                json.dumps(item["finite_dimensions"], sort_keys=True, separators=(",", ":"))
                for item in samples
            }
            metrics.append(
                {
                    "name": name,
                    "series_count": len(samples),
                    "value_sum": round(sum(float(item["value"]) for item in samples), 6),
                    "label_keys": sorted({key for item in samples for key in item["label_keys"]})[
                        :40
                    ],
                    "finite_dimensions": [json.loads(item) for item in sorted(dimension_rows)],
                }
            )
        return {
            "http_status": observation.get("http_status"),
            "prometheus_api_status_ok": response.get("status") == "success",
            "query_series_count": len(series),
            "metrics": metrics,
            "error_category": observation.get("error_category"),
            "json_valid": observation.get("json_valid"),
            "body_truncated": observation.get("truncated"),
        }

    def signal_boundary_diagnostic(
        self,
        *,
        route_status: int | None,
        request_id: str,
        trace_id: str,
        sink_observations: dict[str, dict[str, object]],
        receiver_health_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            api_record = self.inspect_service("api")
            environment = (api_record.get("Config") or {}).get("Env") or []
            config = dict(
                item.split("=", 1)
                for item in environment
                if isinstance(item, str)
                and "=" in item
                and item.split("=", 1)[0]
                in {
                    "TELEMETRY_ENABLED",
                    "TELEMETRY_OTLP_ENDPOINT",
                    "SERVICE_RELEASE",
                    "ENVIRONMENT",
                }
            )
        except Exception:
            config = {}
        endpoint = urlparse(config.get("TELEMETRY_OTLP_ENDPOINT", ""))
        api_config = {
            "telemetry_enabled": config.get("TELEMETRY_ENABLED") == "true",
            "otlp_endpoint_host_is_collector": endpoint.hostname == "collector",
            "environment_is_demo": config.get("ENVIRONMENT") == "demo",
            "release_matches_source": config.get("SERVICE_RELEASE") == self.stack.expected_sha,
        }
        services: dict[str, dict[str, object]] = {}
        for name in (
            "api",
            "worker",
            "mysql",
            "frontend",
            "gateway",
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
            "gateway_log_rotate",
        ):
            try:
                record = self.inspect_service(name)
                state = record.get("State") or {}
                services[name] = {
                    "status": state.get("Status"),
                    "restart_count": record.get("RestartCount"),
                    "oom_killed": bool(state.get("OOMKilled")),
                    "exit_code": state.get("ExitCode"),
                    "error_present": bool(state.get("Error")),
                    "health": (state.get("Health") or {}).get("Status"),
                }
            except Exception:
                services[name] = {"inspection_available": False}
        return {
            "route": {
                "http_status": route_status,
                "request_id_present": bool(request_id),
                "request_id_format_ok": bool(re.fullmatch(r"[a-f0-9]{32}", request_id)),
                "trace_id_present": bool(trace_id),
                "trace_id_format_ok": bool(re.fullmatch(r"[a-f0-9]{32}", trace_id)),
            },
            "api_config": api_config,
            "loki_query": sink_observations.get("loki_query", {"failure_category": "not_observed"}),
            "loki_index_series": sink_observations.get(
                "loki_index_series", {"failure_category": "not_observed"}
            ),
            "api_native_log": sink_observations.get(
                "api_native_log", {"failure_category": "not_observed"}
            ),
            "worker_native_log": sink_observations.get(
                "worker_native_log", {"failure_category": "not_observed"}
            ),
            "tempo": sink_observations.get("tempo", {"failure_category": "not_observed"}),
            "receiver_health": (
                receiver_health_summary
                if isinstance(receiver_health_summary, dict)
                else {"available": False}
            ),
            "collector_counters": self._safe_collector_counter_snapshot(),
            "operations_probe": getattr(self, "last_operations_probe", {"available": False}),
            "services": services,
        }

    def _safe_collector_counter_snapshot(self) -> dict[str, object]:
        try:
            return self._collector_counter_snapshot()
        except Exception:
            return {"available": False}

    def volume_snapshot(self, *, deadline: float | None = None) -> dict[str, dict[str, object]]:
        def bounded_timeout() -> float:
            if deadline is None:
                return 180
            if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
                raise AcceptanceFailure("The volume snapshot deadline is invalid")
            remaining = float(deadline) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise AcceptanceFailure("The volume snapshot deadline expired")
            return min(180, remaining)

        listed = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.stack.project}",
                ],
                timeout=bounded_timeout(),
            ),
            "The UUID-owned Compose volumes could not be listed",
        )
        names = [name for name in listed.splitlines() if name]
        records = (
            acceptance_json(
                acceptance_invoke(
                    ["docker", "volume", "inspect", *names], timeout=bounded_timeout()
                ),
                "The UUID-owned volume identities could not be read",
            )
            if names
            else []
        )
        if len(records) != len(names):
            raise AcceptanceFailure("A UUID-owned Compose volume identity is missing")
        result = {}
        for item in records:
            labels = item.get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.stack.project:
                raise AcceptanceFailure("A volume failed the UUID ownership guard")
            result[item["Name"]] = {
                "driver": item.get("Driver"),
                "mountpoint": item.get("Mountpoint"),
                "options": item.get("Options") or {},
                "labels": labels,
                "created": item.get("CreatedAt"),
            }
        return result

    def inspect_service(
        self,
        service: str,
        *,
        timeout: float = 180,
        deadline: float | None = None,
    ) -> dict[str, object]:
        def bounded_timeout() -> float:
            if deadline is None:
                return timeout
            if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
                raise AcceptanceFailure("The container inspection deadline is invalid")
            remaining = float(deadline) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise AcceptanceFailure("The container inspection deadline expired")
            return min(timeout, remaining)

        identifiers = acceptance_success(
            self.compose("ps", "-a", "-q", service, timeout=bounded_timeout()),
            f"The UUID-owned {service} container is missing",
        ).splitlines()
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identifiers[0]):
            raise AcceptanceFailure(f"The UUID-owned {service} container identity is invalid")
        identifier = identifiers[0]
        records = acceptance_json(
            acceptance_invoke(["docker", "inspect", identifier], timeout=bounded_timeout()),
            f"The UUID-owned {service} container could not be inspected",
        )
        if len(records) != 1 or (
            records[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
            != self.stack.project
        ):
            raise AcceptanceFailure(f"The {service} container failed its exact owner check")
        return records[0]

    def wait_targets(self, *, deadline: float | None = None) -> list[dict[str, str]]:
        wait_seconds = 90.0
        if deadline is not None:
            if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
                raise AcceptanceFailure("The Prometheus target wait deadline is invalid")
            remaining = float(deadline) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise AcceptanceFailure("The Prometheus target wait deadline expired")
            wait_seconds = min(wait_seconds, remaining)
        expected = {
            "api",
            "collector",
            "application-otel",
            "local-alert-receiver",
            "prometheus-self",
            "loki-self",
            "tempo-self",
            "grafana-self",
            "alertmanager-self",
        }
        observed = []

        def ready() -> bool:
            nonlocal observed
            response = self.api_json(
                "http://prometheus:9090/api/v1/targets?state=active", deadline=deadline
            )
            observed = response.get("data", {}).get("activeTargets", [])
            up = {
                target.get("labels", {}).get("job")
                for target in observed
                if target.get("health") == "up"
            }
            return up == expected and len(observed) == len(expected)

        self.stack.wait_for(
            ready,
            seconds=wait_seconds,
            failure="The complete Prometheus target matrix did not become UP",
        )
        return [
            {
                "job": target["labels"]["job"],
                "health": target["health"],
                "instance": target["labels"]["instance"],
            }
            for target in sorted(observed, key=lambda item: item["labels"]["job"])
        ]

    def worker_metric_state(self, *, deadline: float | None = None):
        numeric_deadline = None
        if deadline is not None:
            if type(deadline) not in (int, float):
                raise AcceptanceFailure("The worker metric query deadline is invalid")
            try:
                numeric_deadline = float(deadline)
            except (OverflowError, ValueError):
                raise AcceptanceFailure("The worker metric query deadline is invalid") from None
            if not math.isfinite(numeric_deadline):
                raise AcceptanceFailure("The worker metric query deadline is invalid")

        def request_json(url: str) -> dict[str, object]:
            if numeric_deadline is None:
                return self.api_json(url)
            if time.monotonic() >= numeric_deadline:
                raise AcceptanceFailure("The worker metric query deadline expired")
            return self.api_json(url, deadline=numeric_deadline)

        self.last_worker_metric_query_diagnostic = {"query_stage": "metric_names"}
        response = request_json("http://prometheus:9090/api/v1/label/__name__/values")
        api_status = response.get("status") if isinstance(response, dict) else None
        self.last_worker_metric_query_diagnostic["api_status"] = (
            api_status
            if isinstance(api_status, str) and api_status in {"success", "error"}
            else "invalid"
        )
        if api_status != "success":
            raise AcceptanceFailure("Prometheus returned an unsuccessful worker metric-name query")
        metric_names = response.get("data")
        if not isinstance(metric_names, list):
            self.last_worker_metric_query_diagnostic["api_status"] = "invalid"
            raise AcceptanceFailure("Prometheus returned an invalid worker metric-name response")
        names = sorted(
            name
            for name in metric_names
            if isinstance(name, str) and name.startswith("prl_worker_")
        )
        roles = {
            "started": "started_timestamp_seconds",
            "heartbeat": "heartbeat_timestamp_seconds",
            "last_successful_scan": "last_successful_scan_timestamp_seconds",
            "last_progress": "last_progress_timestamp_seconds",
            "backlog_count": "backlog_count",
            "oldest_overdue": "oldest_overdue_seconds",
            "successful_scans": "successful_scans_total",
        }
        selected = {
            role: next((name for name in names if fragment in name), "")
            for role, fragment in roles.items()
        }
        selected = {role: name for role, name in selected.items() if name}
        self.last_worker_metric_query_diagnostic = {
            "query_stage": "metric_series",
            "selected_roles": sorted(selected),
            "candidate_counts": {},
            "sample_roles": [],
            "sample_timestamps": {},
            "finite_sample_timestamp_roles": [],
            "invalid_sample_timestamp_roles": [],
            "finite_value_roles": [],
            "nonfinite_value_roles": [],
            "invalid_sample_roles": [],
            "ambiguous_roles": [],
        }
        state = {}
        for role, metric_name in selected.items():
            self.last_worker_metric_query_diagnostic["query_role"] = role
            response = request_json(
                "http://prometheus:9090/api/v1/query?" + urlencode({"query": metric_name})
            )
            api_status = response.get("status") if isinstance(response, dict) else None
            self.last_worker_metric_query_diagnostic["api_status"] = (
                api_status
                if isinstance(api_status, str) and api_status in {"success", "error"}
                else "invalid"
            )
            if api_status != "success":
                raise AcceptanceFailure("Prometheus returned an unsuccessful worker metric query")
            metric_data = response.get("data")
            samples = metric_data.get("result") if isinstance(metric_data, dict) else None
            if not isinstance(samples, list):
                self.last_worker_metric_query_diagnostic["api_status"] = "invalid"
                raise AcceptanceFailure("Prometheus returned an invalid worker metric response")
            candidates = []
            for sample in samples:
                if not isinstance(sample, dict):
                    continue
                labels = sample.get("metric")
                if not isinstance(labels, dict):
                    continue
                if (
                    labels.get("service_name") == "worker"
                    and labels.get("deployment_environment_name") == "demo"
                    and labels.get("worker_slot") == "1"
                ):
                    candidates.append(sample)
            candidate_counts = self.last_worker_metric_query_diagnostic["candidate_counts"]
            if isinstance(candidate_counts, dict):
                candidate_counts[role] = len(candidates)
            if len(candidates) > 1:
                ambiguous_roles = self.last_worker_metric_query_diagnostic["ambiguous_roles"]
                if isinstance(ambiguous_roles, list):
                    ambiguous_roles.append(role)
                raise AcceptanceFailure(
                    f"Prometheus returned multiple worker series for the {role} metric"
                )
            if candidates:
                sample_value = candidates[0].get("value")
                if not isinstance(sample_value, list) or len(sample_value) != 2:
                    invalid_roles = self.last_worker_metric_query_diagnostic["invalid_sample_roles"]
                    if isinstance(invalid_roles, list):
                        invalid_roles.append(role)
                    continue
                try:
                    sample_timestamp = float(sample_value[0])
                    value = float(sample_value[1])
                except (TypeError, ValueError):
                    invalid_roles = self.last_worker_metric_query_diagnostic["invalid_sample_roles"]
                    if isinstance(invalid_roles, list):
                        invalid_roles.append(role)
                    continue
                state[role] = {
                    "name": metric_name,
                    "labels": candidates[0]["metric"],
                    "sample_timestamp": sample_timestamp,
                    "value": value,
                }
                sample_roles = self.last_worker_metric_query_diagnostic["sample_roles"]
                if isinstance(sample_roles, list):
                    sample_roles.append(role)
                sample_timestamps = self.last_worker_metric_query_diagnostic["sample_timestamps"]
                if (
                    math.isfinite(sample_timestamp)
                    and 0 < sample_timestamp < 10_000_000_000
                    and isinstance(sample_timestamps, dict)
                ):
                    sample_timestamps[role] = sample_timestamp
                    finite_timestamp_roles = self.last_worker_metric_query_diagnostic[
                        "finite_sample_timestamp_roles"
                    ]
                    if isinstance(finite_timestamp_roles, list):
                        finite_timestamp_roles.append(role)
                else:
                    invalid_timestamp_roles = self.last_worker_metric_query_diagnostic[
                        "invalid_sample_timestamp_roles"
                    ]
                    if isinstance(invalid_timestamp_roles, list):
                        invalid_timestamp_roles.append(role)
                numeric_roles = self.last_worker_metric_query_diagnostic[
                    "finite_value_roles" if math.isfinite(value) else "nonfinite_value_roles"
                ]
                if isinstance(numeric_roles, list):
                    numeric_roles.append(role)
        self.last_worker_metric_query_diagnostic["available_roles"] = sorted(state)
        self.last_worker_metric_query_diagnostic["query_stage"] = "complete"
        if numeric_deadline is not None and time.monotonic() >= numeric_deadline:
            raise AcceptanceFailure("The worker metric query completed after its deadline")
        return selected, state

    @staticmethod
    def worker_restart_observation_summary(
        previous_state: dict[str, dict[str, object]],
        current_state: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        identity = {
            "service_name": "worker",
            "deployment_environment_name": "demo",
            "worker_slot": "1",
        }

        def sample(state: dict[str, dict[str, object]], role: str) -> dict[str, object] | None:
            sample = state.get(role)
            if not isinstance(sample, dict):
                return None
            value = sample.get("value")
            metric_name = sample.get("name")
            labels = sample.get("labels")
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value < 10_000_000_000
                or not isinstance(metric_name, str)
                or not metric_name.startswith("prl_worker_")
                or not isinstance(labels, dict)
                or any(labels.get(name) != expected for name, expected in identity.items())
            ):
                return None
            return sample

        def value(state: dict[str, dict[str, object]], role: str) -> float | None:
            selected = sample(state, role)
            if selected is None:
                return None
            metric_value = selected.get("value")
            return float(metric_value) if type(metric_value) in (int, float) else None

        def same_series(role: str) -> bool:
            previous_sample = sample(previous_state, role)
            current_sample = sample(current_state, role)
            return (
                previous_sample is not None
                and current_sample is not None
                and previous_sample.get("name") == current_sample.get("name")
                and previous_sample.get("labels") == current_sample.get("labels")
            )

        previous_started = value(previous_state, "started")
        started = value(current_state, "started")
        previous_scan = value(previous_state, "last_successful_scan")
        scan = value(current_state, "last_successful_scan")
        previous_progress = value(previous_state, "last_progress")
        progress = value(current_state, "last_progress")
        started_series_matches = same_series("started")
        scan_series_matches = same_series("last_successful_scan")
        progress_series_matches = same_series("last_progress")
        return {
            "previous_started_timestamp_seconds": (
                round(previous_started, 3) if previous_started is not None else None
            ),
            "started_timestamp_seconds": round(started, 3) if started is not None else None,
            "started_advanced": (
                started_series_matches
                and previous_started is not None
                and started is not None
                and started > previous_started
            ),
            "started_series_matches": started_series_matches,
            "started_unchanged": (
                started_series_matches
                and previous_started is not None
                and started is not None
                and started == previous_started
            ),
            "previous_last_successful_scan_timestamp_seconds": (
                round(previous_scan, 3) if previous_scan is not None else None
            ),
            "last_successful_scan_timestamp_seconds": (
                round(scan, 3) if scan is not None else None
            ),
            "last_successful_scan_series_matches": scan_series_matches,
            "last_successful_scan_retained": (
                scan_series_matches
                and previous_scan is not None
                and scan is not None
                and scan == previous_scan
            ),
            "previous_last_progress_timestamp_seconds": (
                round(previous_progress, 3) if previous_progress is not None else None
            ),
            "last_progress_timestamp_seconds": (
                round(progress, 3) if progress is not None else None
            ),
            "last_progress_series_matches": progress_series_matches,
            "last_progress_retained": (
                progress_series_matches
                and previous_progress is not None
                and progress is not None
                and progress == previous_progress
            ),
            "series_identity_matches": (
                started_series_matches and scan_series_matches and progress_series_matches
            ),
        }

    @staticmethod
    def worker_restart_retained_prior_points(summary: dict[str, object]) -> bool:
        return (
            isinstance(summary, dict)
            and summary.get("started_advanced") is True
            and summary.get("series_identity_matches") is True
            and summary.get("last_successful_scan_retained") is True
            and summary.get("last_progress_retained") is True
            and all(
                type(summary.get(name)) in (int, float)
                and math.isfinite(summary[name])
                and 0 < summary[name] < 10_000_000_000
                for name in (
                    "previous_started_timestamp_seconds",
                    "started_timestamp_seconds",
                    "previous_last_successful_scan_timestamp_seconds",
                    "last_successful_scan_timestamp_seconds",
                    "previous_last_progress_timestamp_seconds",
                    "last_progress_timestamp_seconds",
                )
            )
        )

    @staticmethod
    def worker_started_value_matches_container_generation(
        value: object,
        container_started_at: object,
    ) -> bool:
        if type(value) not in (int, float) or type(container_started_at) not in (int, float):
            return False
        try:
            metric_value = float(value)
            started_at = float(container_started_at)
        except (OverflowError, ValueError):
            return False
        if (
            not math.isfinite(metric_value)
            or not math.isfinite(started_at)
            or not 0 < metric_value < 10_000_000_000
            or not 0 < started_at < 10_000_000_000
        ):
            return False
        return started_at - 1.0 <= metric_value <= started_at + 10.0

    def worker_series_schema(self) -> list[dict[str, object]]:
        response = self.api_json(
            "http://prometheus:9090/api/v1/series?"
            + urlencode({"match[]": '{__name__=~"prl_worker_.*"}'})
        )
        forbidden = {
            "request_id",
            "trace_id",
            "worker_id",
            "run_id",
            "current_run_id",
            "release",
            "source_sha",
            "uuid",
            "service_instance_id",
        }
        series = []
        for labels in response.get("data", []):
            keys = set(labels)
            if keys & forbidden:
                raise AcceptanceFailure("A worker metric exposed an unbounded identity label")
            if (
                labels.get("service_name") != "worker"
                or labels.get("deployment_environment_name") != "demo"
                or labels.get("worker_slot") != "1"
            ):
                raise AcceptanceFailure(
                    "A worker metric lost its finite service/environment/slot labels"
                )
            series.append(
                {
                    "name": labels.get("__name__"),
                    "labels": sorted(keys),
                    "service_name": labels["service_name"],
                    "deployment_environment_name": labels["deployment_environment_name"],
                    "worker_slot": labels["worker_slot"],
                }
            )
        if not series:
            raise AcceptanceFailure("The Collector exposed no worker metric series")
        target_info = self.api_json(
            "http://prometheus:9090/api/v1/series?"
            + urlencode({"match[]": '{__name__="target_info"}'})
        ).get("data", [])
        target_forbidden = forbidden | {"service_instance_id", "service_version", "service_id"}
        target_info_labels = []
        for labels in target_info:
            keys = set(labels)
            if keys & target_forbidden:
                raise AcceptanceFailure("Collector target_info exposed an unbounded identity")
            target_info_labels.append(sorted(keys))
        self.target_info_schema = target_info_labels
        return sorted(series, key=lambda item: str(item["name"]))

    def query_loki_request(
        self,
        request_id: str,
        trace_id: str,
        *,
        start_ns: int | None = None,
    ) -> dict[str, object]:
        query = self.observe_loki_request(request_id, trace_id, start_ns=start_ns)
        index = self.observe_loki_index_series(expected_request_id=request_id)
        if query.get("failure_category") is not None:
            raise AcceptanceFailure("A gateway request did not produce one correlated Loki event")
        if index.get("failure_category") is not None:
            raise AcceptanceFailure("Loki gateway series did not retain the finite index schema")
        return {
            "query_result_stream_keys": query.get("query_result_stream_keys", []),
            "query_result_resource_identity": query.get("query_result_resource_identity", []),
            "structured_field_keys": query.get("structured_field_keys", []),
            "event": "gateway.request",
            "request_id_match": query.get("request_id_match") is True,
            "trace_id_format_ok": query.get("trace_id_format_ok") is True,
            "release_match": query.get("release_match") is True,
            "request_event_timestamp_unix_ns": query.get("request_event_timestamp_unix_ns"),
            "index_label_keys": index.get("index_label_keys", []),
            "index_resource_identity": index.get("index_resource_identity", []),
            "index_series_count": index.get("series_count"),
            "request_id_indexed": index.get("request_id_indexed") is True,
        }

    def query_loki_healthchecks(self, *, start_ns: int | None = None) -> dict[str, object]:
        query = (
            '{service_name="gateway",deployment_environment_name="demo"} '
            '| event="gateway.healthcheck"'
        )
        now_ns = time.time_ns()
        query_start_ns = start_ns if start_ns is not None else now_ns - 600_000_000_000
        params = urlencode(
            {
                "query": query,
                "start": str(query_start_ns),
                "end": str(now_ns + 60_000_000_000),
                "limit": "100",
            }
        )
        response = self.api_json("http://loki:3100/loki/api/v1/query_range?" + params)
        events = []
        for stream in response.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for entry in stream.get("values", []):
                try:
                    entry_timestamp_ns = int(entry[0])
                except (TypeError, ValueError, IndexError):
                    raise AcceptanceFailure("A Loki healthcheck had an invalid timestamp") from None
                if entry_timestamp_ns < query_start_ns:
                    continue
                try:
                    body = json.loads(entry[1])
                except (TypeError, json.JSONDecodeError, IndexError):
                    body = {}
                metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
                fields = {**body, **metadata}
                if (
                    labels.get("service_name") != "gateway"
                    or labels.get("deployment_environment_name") != "demo"
                    or fields.get("event") != "gateway.healthcheck"
                    or fields.get("classification") != "out_of_scope"
                ):
                    raise AcceptanceFailure("A Loki healthcheck lost its finite event contract")
                timestamp_value = fields.get("timestamp")
                try:
                    if not isinstance(timestamp_value, str):
                        raise ValueError
                    event_time = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
                    if event_time.tzinfo is None or event_time.utcoffset() is None:
                        raise ValueError
                    event_timestamp_ns = int(event_time.astimezone(UTC).timestamp() * 1_000_000_000)
                except (ValueError, OverflowError):
                    raise AcceptanceFailure(
                        "A Loki healthcheck had no valid producer UTC timestamp"
                    ) from None
                if event_timestamp_ns < query_start_ns:
                    continue
                events.append((event_timestamp_ns, entry_timestamp_ns, fields))
        events.sort(key=lambda event: event[0])
        if len(events) < 2:
            raise AcceptanceFailure("Loki did not receive recurring genuine gateway healthchecks")
        interval = (events[-1][0] - events[-2][0]) / 1_000_000_000
        if not 1 <= interval <= 15:
            raise AcceptanceFailure("The Loki gateway healthcheck heartbeat cadence changed")
        return {
            "event": "gateway.healthcheck",
            "classification": "out_of_scope",
            "labels": {"service_name": "gateway", "deployment_environment_name": "demo"},
            "observed_events": len(events),
            "interval_seconds": round(interval, 2),
            "query_start_unix_ns": query_start_ns,
            "latest_event_unix_ns": events[-1][0],
            "latest_entry_timestamp_unix_ns": events[-1][1],
        }

    def write_probe_compose(self) -> None:
        gateway = self.inspect_service("gateway")
        networks = gateway.get("NetworkSettings", {}).get("Networks", {})
        ops_networks = [
            (name, values.get("IPAddress"))
            for name, values in networks.items()
            if name.endswith("_ops_grafana")
        ]
        if len(ops_networks) != 1 or not ops_networks[0][1]:
            raise AcceptanceFailure("The UUID-owned operations network identity is unavailable")
        _ops_network, gateway_ip = ops_networks[0]
        positive_script = """import base64
import http.client
import json
import socket
import ssl

class Connection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)

def failure_category(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return "certificate_validation"
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "missing_file"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, (OSError, http.client.HTTPException)):
        return "transport_error"
    return "request_error"

def json_kind(value, valid):
    if not valid:
        return "invalid"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "null"
    return "scalar"

context = ssl.create_default_context(cafile="/run/prl/ops.crt")
user = open("/run/prl/admin-user", encoding="utf-8").read().strip()
password = open("/run/prl/admin-password", encoding="utf-8").read().strip()
credential = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
stage_observations = {}
failure_categories = set()
last_stage = None

def request(stage, path, authenticated=False):
    global last_stage
    last_stage = stage
    connection = None
    try:
        connection = Connection("ops.example.invalid", 8443, timeout=4, context=context)
        headers = {"Host": "ops.example.invalid", "Connection": "close"}
        if authenticated:
            headers["Authorization"] = credential
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        status = response.status
        raw = response.read()
        try:
            value = json.loads(raw)
            valid = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            value = None
            valid = False
        observation = {"http_status": status, "json_kind": json_kind(value, valid)}
        if isinstance(value, list):
            observation["list_count"] = min(len(value), 1000)
        stage_observations[stage] = observation
        return status, value
    except Exception as error:
        category = failure_category(error)
        failure_categories.add(category)
        stage_observations[stage] = {
            "http_status": None,
            "json_kind": "unavailable",
            "failure_category": category,
        }
        return None, None
    finally:
        if connection is not None:
            connection.close()

health, _ = request("health", "/api/health")
unauthenticated, _ = request("anonymous_org", "/api/org")
admin_status, _ = request("admin_org", "/api/org", True)
datasource_status, datasources = request("datasources", "/api/datasources", True)
dashboard_status, dashboards = request("dashboards", "/api/search?type=dash-db", True)
ops_business, _ = request("ops_business", "/api/v1/health-shaped", False)
ops_health, _ = request("ops_health", "/health/gateway", False)

datasource_uids = []
unknown_uid_count = 0
malformed_item_count = 0
if isinstance(datasources, list):
    for item in datasources:
        if not isinstance(item, dict):
            malformed_item_count += 1
            continue
        uid = item.get("uid")
        if isinstance(uid, str) and uid in {"prl-loki", "prl-prometheus", "prl-tempo"}:
            datasource_uids.append(uid)
        elif isinstance(uid, str) and uid:
            unknown_uid_count += 1
        else:
            malformed_item_count += 1

dashboard_uids = sorted(
    item.get("uid") for item in dashboards
    if isinstance(dashboards, list) and isinstance(item, dict)
    and isinstance(item.get("uid"), str) and item.get("uid") in {
        "prl-gateway", "prl-api-errors", "prl-db-pool", "prl-worker-progress", "prl-telemetry-loss"
    }
)
loki_source = next(
    (item for item in datasources if isinstance(item, dict) and item.get("uid") == "prl-loki"),
    {},
) if isinstance(datasources, list) else {}
tempo_source = next(
    (item for item in datasources if isinstance(item, dict) and item.get("uid") == "prl-tempo"),
    {},
) if isinstance(datasources, list) else {}
trace_link_config_ok = any(
    isinstance(field, dict)
    and field.get("name") == "TraceID"
    and field.get("matcherType") == "label"
    and field.get("matcherRegex") == "^trace_id$"
    and field.get("datasourceUid") == "prl-tempo"
    and field.get("url") == "$" + "{__value.raw}"
    and field.get("urlDisplayLabel") == "View trace"
    for field in (loki_source.get("jsonData") or {}).get("derivedFields", [])
)
tempo_trace_settings = (tempo_source.get("jsonData") or {}).get("tracesToLogsV2", {})
tempo_trace_filter_config_ok = (
    tempo_trace_settings.get("datasourceUid") == "prl-loki"
    and tempo_trace_settings.get("filterByTraceID") is True
)
tls_categories = {"certificate_validation", "tls_error"}
tls_errors = failure_categories & tls_categories
print("TASK6_PROBE:" + json.dumps({
    "health": health,
    "unauth_org": unauthenticated,
    "admin_org": admin_status,
    "datasource_status": datasource_status,
    "datasource_uids": sorted(datasource_uids),
    "trace_link_config_ok": trace_link_config_ok,
    "tempo_trace_filter_config_ok": tempo_trace_filter_config_ok,
    "dashboard_status": dashboard_status,
    "dashboard_uids": dashboard_uids,
    "datasource_unknown_uid_count": unknown_uid_count,
    "datasource_malformed_item_count": malformed_item_count,
    "ops_business_status": ops_business,
    "ops_health_status": ops_health,
    "stage_observations": stage_observations,
    "last_stage": last_stage,
    "tls_exception_category": (
        "certificate_validation"
        if "certificate_validation" in tls_errors
        else "tls_error"
        if "tls_error" in tls_errors
        else None
    ),
    "failure_categories": sorted(failure_categories),
}, separators=(",", ":")))
"""

        negative_script = """import http.client
import json
import ssl

context = ssl.create_default_context(cafile="/run/prl/ops.crt")
connection = http.client.HTTPSConnection(
    "ops.example.invalid", 8443, timeout=4, context=context
)
connection.request("GET", "/api/org", headers={
    "Host": "ops.example.invalid",
    "X-Forwarded-For": "127.0.0.1",
    "X-Real-IP": "127.0.0.1",
    "Forwarded": "for=127.0.0.1",
    "Connection": "close",
})
response = connection.getresponse()
status = response.status
response.read()
connection.close()
print("TASK6_PROBE:" + json.dumps({"spoofed_forwarded_status": status}))
"""

        def mount(source: Path, target: str) -> str:
            return (
                "      - type: bind\n"
                f"        source: {json.dumps(str(source.resolve()))}\n"
                f"        target: {target}\n"
                "        read_only: true\n"
            )

        lines = [
            "services:",
            "  task6_ops_probe:",
            f"    image: {json.dumps('prl-backend:' + self.stack.image_tag)}",
            "    profiles: [task6-ops-probe]",
            "    network_mode: service:gateway",
            "    depends_on: {gateway: {condition: service_started}}",
            f"    entrypoint: {json.dumps(['python', '-c', positive_script])}",
            "    volumes:",
            mount(self.ops_cert, "/run/prl/ops.crt").rstrip(),
            mount(self.admin_user_file, "/run/prl/admin-user").rstrip(),
            mount(self.admin_password_file, "/run/prl/admin-password").rstrip(),
            "    mem_limit: 128m",
            "    memswap_limit: 128m",
            "    cpus: 0.5",
            "    pids_limit: 32",
            "    read_only: true",
            '    tmpfs: ["/tmp:size=8m,mode=1777"]',
            "    cap_drop: [ALL]",
            "    security_opt: [no-new-privileges:true]",
            "  task6_ops_denied_probe:",
            f"    image: {json.dumps('prl-backend:' + self.stack.image_tag)}",
            "    profiles: [task6-ops-denied-probe]",
            "    networks: [ops_grafana]",
            f"    extra_hosts: {json.dumps(['ops.example.invalid:' + gateway_ip])}",
            f"    entrypoint: {json.dumps(['python', '-c', negative_script])}",
            "    volumes:",
            mount(self.ops_cert, "/run/prl/ops.crt").rstrip(),
            "    mem_limit: 128m",
            "    memswap_limit: 128m",
            "    cpus: 0.5",
            "    pids_limit: 32",
            "    read_only: true",
            '    tmpfs: ["/tmp:size=8m,mode=1777"]',
            "    cap_drop: [ALL]",
            "    security_opt: [no-new-privileges:true]",
        ]
        self.probe_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.stack.base.extend(["-f", str(self.probe_file.resolve())])

    def run_probe(self, profile: str, service: str) -> dict[str, object]:
        result = self.compose("--profile", profile, "run", "--rm", "-T", service)
        if result.returncode:
            failure = safe_ops_probe_failure_summary(
                result.returncode, result.stdout, result.stderr
            )
            self.last_operations_probe = {"available": True, "command_failure": failure}
            print(
                "TASK6_OPS_PROBE_FAILURE:"
                + json.dumps(self.last_operations_probe, sort_keys=True, separators=(",", ":"))
            )
            raise AcceptanceFailure("A bounded UUID-owned operations probe failed")
        for line in reversed((result.stdout or "").splitlines()):
            if line.startswith("TASK6_PROBE:"):
                try:
                    payload = json.loads(line.removeprefix("TASK6_PROBE:"))
                except json.JSONDecodeError:
                    break
                summary = safe_ops_probe_result_summary(payload)
                self.last_operations_probe = summary
                print(
                    "TASK6_OPS_PROBE_SUMMARY:"
                    + json.dumps(summary, sort_keys=True, separators=(",", ":"))
                )
                if isinstance(payload, dict):
                    return payload
                break
        raise AcceptanceFailure("An operations probe returned no sanitized result")

    def gateway_operations_records(self) -> list[dict[str, object]]:
        gateway_id = acceptance_success(
            self.compose("ps", "-q", "gateway"),
            "The UUID-owned gateway container is missing",
        )
        result = acceptance_invoke(["docker", "logs", gateway_id], timeout=15)
        if result.returncode:
            raise AcceptanceFailure("The sanitized gateway operations records could not be read")
        required = {
            "timestamp",
            "service",
            "environment",
            "release",
            "event",
            "route",
            "method",
            "status",
            "duration_seconds",
            "classification",
        }
        records = []
        for line in (result.stdout + result.stderr).splitlines():
            if '"event":"gateway.operations"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                set(record) != required
                or record.get("service") != "gateway"
                or record.get("environment") != "demo"
                or record.get("release") != self.stack.expected_sha
                or record.get("event") != "gateway.operations"
                or record.get("route") != "__unmatched__"
                or record.get("classification") != "out_of_scope"
            ):
                raise AcceptanceFailure(
                    "An OPS-vhost record violated its finite out-of-scope schema"
                )
            records.append(record)
        return records

    def synthetic_alert_summaries(self) -> list[dict[str, str]]:
        name = "Task6SyntheticDeliveryProbe"
        for status in ("firing", "resolved"):
            self.api_json(
                "http://local_alert_receiver:8088/alerts",
                method="POST",
                body={
                    "alerts": [
                        {
                            "status": status,
                            "labels": {"alertname": name, "severity": "warning"},
                            "annotations": {"summary": "Synthetic local delivery check"},
                        }
                    ]
                },
            )
        summaries = self.api_json("http://local_alert_receiver:8088/alerts")["alerts"]
        matching = [item for item in summaries if item.get("alertname") == name]
        if [item.get("status") for item in matching] != ["firing", "resolved"]:
            raise AcceptanceFailure("The receiver lost synthetic firing/resolved summaries")
        for item in matching:
            if set(item) != {"alertname", "severity", "status", "received_at"}:
                raise AcceptanceFailure("The receiver returned fields outside its safe summary")
            if item.get("severity") != "warning":
                raise AcceptanceFailure("The receiver lost the finite alert severity")
            try:
                datetime.fromisoformat(item["received_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                raise AcceptanceFailure("The receiver summary has no receipt timestamp") from None
        return matching

    def start(self) -> None:
        self.stack.wait_gateway = self.wait_app_gateway
        original_worker_log_output = self.stack.worker_log_output

        def capture_worker_progress() -> str:
            output = original_worker_log_output()
            progress = latest_worker_progress(output, require_heartbeat=False)
            observation: dict[str, object] = {"parsed": progress is not None}
            if progress is not None:
                observation["successful_scans"] = progress.successful_scans
                if self.observed_worker_id is None:
                    self.observed_worker_id = progress.worker_id
                    observation["same_worker"] = True
                else:
                    observation["same_worker"] = progress.worker_id == self.observed_worker_id
            self.heartbeat_observations.append(observation)
            del self.heartbeat_observations[:-8]
            if progress is None and self.worker_stream_diagnostic is None:
                worker_id = acceptance_success(
                    self.compose("ps", "-q", "worker"),
                    "The UUID-owned worker container is missing during stream diagnostics",
                )
                if re.fullmatch(r"[0-9a-f]{64}", worker_id):
                    inspected = acceptance_json(
                        acceptance_invoke(["docker", "inspect", worker_id]),
                        "The UUID-owned worker configuration could not be inspected",
                    )
                    direct = acceptance_invoke(
                        ["docker", "logs", "--tail", "400", worker_id], timeout=15
                    )

                    def event_class(stream: str) -> str:
                        lines = [line for line in stream.splitlines() if line.strip()]
                        if not lines:
                            return "empty"
                        try:
                            record = json.loads(lines[-1])
                        except json.JSONDecodeError:
                            return "non_json"
                        if not isinstance(record, dict):
                            return "non_object_json"
                        event = record.get("event")
                        if event in {
                            "starting",
                            "ready",
                            "heartbeat",
                            "scan",
                            "candidate",
                            "stopping",
                            "stopped",
                            "fatal",
                        }:
                            return "known_event"
                        return "unknown_event"

                    stdout_progress = latest_worker_progress(direct.stdout, require_heartbeat=False)
                    stderr_progress = latest_worker_progress(direct.stderr, require_heartbeat=False)
                    stream_diagnostic: dict[str, object] = {
                        "compose_merged_parsed": progress is not None,
                        "compose_log_succeeded": True,
                        "direct_log_succeeded": direct.returncode == 0,
                        "container_tty": bool((inspected[0].get("Config") or {}).get("Tty")),
                        "direct_stdout_parsed": stdout_progress is not None,
                        "direct_stdout_scans": (
                            stdout_progress.successful_scans
                            if stdout_progress is not None
                            else None
                        ),
                        "direct_stdout_tail": event_class(direct.stdout),
                        "direct_stderr_parsed": stderr_progress is not None,
                        "direct_stderr_tail": event_class(direct.stderr),
                    }
                    if (
                        stdout_progress is not None
                        or stderr_progress is not None
                        or len(self.heartbeat_observations) >= 8
                    ):
                        self.worker_stream_diagnostic = stream_diagnostic
            return output

        self.stack.worker_log_output = capture_worker_progress
        original_wait_heartbeat = self.stack.wait_heartbeat

        def wait_heartbeat_with_safe_diagnostics(*, excluded_worker_id: str | None = None):
            try:
                return original_wait_heartbeat(excluded_worker_id=excluded_worker_id)
            except AcceptanceFailure:
                try:
                    diagnostic = self.safe_worker_diagnostic()
                except AcceptanceFailure:
                    diagnostic = {"available": False}
                print("TASK6_WORKER_DIAGNOSTIC:" + json.dumps(diagnostic, separators=(",", ":")))
                raise

        self.stack.wait_heartbeat = wait_heartbeat_with_safe_diagnostics
        self.stack.start()
        self.api_id = acceptance_success(
            self.compose("ps", "-q", "api"),
            "The UUID-owned API container is missing after readiness",
        )
        acceptance_success(
            self.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.stack.expected_sha}",
                "collector",
                timeout=900,
            ),
            "The exact-source Collector image could not be built",
        )
        services = (
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "local_alert_receiver",
            "gateway_log_rotate",
        )
        acceptance_success(
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", *services, timeout=240),
            "The UUID-owned telemetry services did not start",
        )
        self.write_probe_compose()
        self.wait_targets()

    def safe_worker_diagnostic(self) -> dict[str, object]:
        worker_id = acceptance_success(
            self.compose("ps", "-a", "-q", "worker"),
            "The UUID-owned worker container is missing during diagnostics",
        ).splitlines()[-1]
        if not re.fullmatch(r"[0-9a-f]{64}", worker_id):
            raise AcceptanceFailure("The UUID-owned worker identity is invalid during diagnostics")
        inspected = acceptance_json(
            acceptance_invoke(["docker", "inspect", worker_id]),
            "The UUID-owned worker state could not be inspected for diagnostics",
        )
        state = inspected[0].get("State") or {}
        settings: dict[str, str] = {}
        environment = (inspected[0].get("Config") or {}).get("Env") or []
        for item in environment:
            if isinstance(item, str) and "=" in item:
                name, value = item.split("=", 1)
                if name in {
                    "ENVIRONMENT",
                    "WORKER_POLL_INTERVAL_SECONDS",
                    "WORKER_HEARTBEAT_INTERVAL_SECONDS",
                } and re.fullmatch(r"[A-Za-z0-9._-]{1,32}", value):
                    settings[name] = value
        logs = self.compose(
            "logs", "--no-color", "--no-log-prefix", "--tail", "40", "worker", timeout=15
        )
        records: list[dict[str, object]] = []
        for line in (logs.stdout + logs.stderr).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("event") not in {
                "starting",
                "ready",
                "heartbeat",
                "scan",
                "candidate",
                "stopping",
                "stopped",
                "fatal",
            }:
                continue
            record: dict[str, object] = {"event": item["event"]}
            for name in (
                "phase",
                "database_ok",
                "successful_scans",
                "failed_scans",
                "reporting_failures",
            ):
                value = item.get(name)
                if name == "phase" and isinstance(value, str) and len(value) <= 24:
                    record[name] = value
                elif name != "phase" and type(value) in (bool, int):
                    record[name] = value
            error_code = item.get("error_code")
            if isinstance(error_code, str) and re.fullmatch(r"[a-z_]{1,40}", error_code):
                record["error_code"] = error_code
            reason = item.get("reason")
            if isinstance(reason, str) and reason in {
                "configuration_invalid",
                "schema_incompatible",
                "deadline_exceeded",
                "unexpected_failure",
            }:
                record["reason"] = reason
            last_scan = item.get("last_successful_scan")
            if isinstance(last_scan, str):
                try:
                    timestamp = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                except ValueError:
                    pass
                else:
                    if timestamp.tzinfo is not None:
                        record["last_successful_scan"] = last_scan
            records.append(record)
        return {
            "running": state.get("Running") is True,
            "status": state.get("Status") if isinstance(state.get("Status"), str) else "unknown",
            "restart_count": inspected[0].get("RestartCount"),
            "oom_killed": state.get("OOMKilled") is True,
            "exit_code": state.get("ExitCode") if isinstance(state.get("ExitCode"), int) else None,
            "settings": settings,
            "heartbeat_observations": self.heartbeat_observations,
            "stream_diagnostic": self.worker_stream_diagnostic,
            "log_command_succeeded": logs.returncode == 0,
            "records": records[-8:],
        }

    def cleanup(self) -> None:
        project = self.stack.project
        if project == "prl-demo" or not re.fullmatch(r"prl-task6-[0-9a-f]{32}", project):
            raise AcceptanceFailure("Refusing cleanup without the UUID-owned project identity")
        listed = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ]
            ),
            "The UUID-owned containers could not be enumerated for cleanup",
        )
        ids = [item for item in listed.splitlines() if item]
        if ids:
            records = acceptance_json(
                acceptance_invoke(["docker", "inspect", *ids]),
                "Container ownership could not be verified before cleanup",
            )
            if any(
                record.get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
                != project
                for record in records
            ):
                raise AcceptanceFailure("A container failed the UUID cleanup guard")
        before = self.volume_snapshot()
        for volume in before.values():
            if volume["labels"].get("com.docker.compose.project") != project:
                raise AcceptanceFailure("A volume failed the UUID cleanup guard")
        if ids:
            acceptance_success(
                self.compose("down", "--remove-orphans", "--timeout", "30", timeout=60),
                "The UUID-owned containers and networks could not be stopped safely",
            )
        if before != self.volume_snapshot():
            raise AcceptanceFailure("Compose cleanup changed or removed a named volume")
        self.stack.assert_demo_untouched()


def test_full_stack_helper_deadlines_cap_runtime_and_keep_default_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FullStackRuntime.__new__(FullStackRuntime)
    compose_timeouts: list[float] = []
    target_waits: list[float] = []
    target_deadlines: list[float | None] = []
    volume_timeouts: list[float] = []
    jobs = (
        "api",
        "collector",
        "application-otel",
        "local-alert-receiver",
        "prometheus-self",
        "loki-self",
        "tempo-self",
        "grafana-self",
        "alertmanager-self",
    )

    class FakeStack:
        project = "prl-task5-bounded"

        def compose(self, *_args: str, timeout: float) -> None:
            compose_timeouts.append(timeout)

        def wait_for(self, predicate: Callable[[], bool], *, seconds: float, failure: str) -> None:
            del failure
            target_waits.append(seconds)
            assert predicate()

    runtime.stack = FakeStack()

    def fake_api_json(_url: str, *, deadline: float | None = None) -> dict[str, object]:
        target_deadlines.append(deadline)
        return {
            "data": {
                "activeTargets": [
                    {"labels": {"job": job, "instance": "fixture"}, "health": "up"}
                    for job in jobs
                ]
            }
        }

    monkeypatch.setattr(runtime, "api_json", fake_api_json)

    def fake_acceptance_invoke(args: list[str], *, timeout: float = 180):
        volume_timeouts.append(timeout)
        if "inspect" in args:
            stdout = json.dumps(
                [
                    {
                        "Name": "prl_task5_bounded_data",
                        "Driver": "local",
                        "Mountpoint": "/fixture",
                        "Options": {},
                        "Labels": {"com.docker.compose.project": FakeStack.project},
                        "CreatedAt": "2026-10-27T00:00:00Z",
                    }
                ]
            )
        else:
            stdout = "prl_task5_bounded_data\n"
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "tests.containers.test_telemetry_stack.acceptance_invoke", fake_acceptance_invoke
    )

    runtime.compose("restart", "collector", timeout=45, deadline=time.monotonic() + 10)
    runtime.compose("ps")
    runtime.wait_targets(deadline=time.monotonic() + 10)
    runtime.wait_targets()
    runtime.volume_snapshot(deadline=time.monotonic() + 10)
    runtime.volume_snapshot()

    assert 0 < compose_timeouts[0] <= 10
    assert compose_timeouts[1] == 180
    assert 0 < target_waits[0] <= 10
    assert target_waits[1] == 90
    assert target_deadlines[0] is not None
    assert target_deadlines[1] is None
    assert len(volume_timeouts) == 4
    assert all(0 < timeout <= 10 for timeout in volume_timeouts[:2])
    assert volume_timeouts[2:] == [180, 180]

    calls_before_expiry = (len(compose_timeouts), len(target_waits), len(volume_timeouts))
    expired_deadline = time.monotonic() - 1
    with pytest.raises(AcceptanceFailure, match="deadline expired"):
        runtime.compose("restart", "collector", deadline=expired_deadline)
    with pytest.raises(AcceptanceFailure, match="deadline expired"):
        runtime.wait_targets(deadline=expired_deadline)
    with pytest.raises(AcceptanceFailure, match="deadline expired"):
        runtime.volume_snapshot(deadline=expired_deadline)
    assert calls_before_expiry == (len(compose_timeouts), len(target_waits), len(volume_timeouts))


@pytest.mark.skipif(
    not _full_stack_enabled(),
    reason="requires all five opt-in Compose runtime gates",
)
def test_receiver_backend_health_component(tmp_path: Path) -> None:
    runtime = FullStackRuntime(tmp_path, use_acceptance_token=False)
    evidence: dict[str, object] = {"stage": "compose_preflight"}
    services = (
        "collector",
        "prometheus",
        "loki",
        "tempo",
        "grafana",
        "alertmanager",
        "local_alert_receiver",
    )
    passed = False

    def safe_service_snapshot() -> tuple[
        dict[str, dict[str, object]], dict[str, dict[str, object]]
    ]:
        states: dict[str, dict[str, object]] = {}
        records_by_service: dict[str, dict[str, object]] = {}
        for name in services:
            listed = runtime.compose("ps", "-a", "-q", name)
            if listed.returncode:
                raise AcceptanceFailure("The UUID-owned component state could not be listed")
            identifiers = [item for item in listed.stdout.splitlines() if item]
            if not identifiers:
                states[name] = {"status": "missing"}
                continue
            if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identifiers[0]):
                raise AcceptanceFailure("A component container identity failed its UUID guard")
            records = acceptance_json(
                acceptance_invoke(["docker", "inspect", identifiers[0]]),
                "The UUID-owned component state could not be inspected",
            )
            if len(records) != 1:
                raise AcceptanceFailure("A component container inspection was incomplete")
            record = records[0]
            labels = (record.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.project") != runtime.stack.project:
                raise AcceptanceFailure("A component container failed its project owner check")
            records_by_service[name] = record
            state = record.get("State") or {}
            states[name] = {
                "status": state.get("Status"),
                "exit_code": state.get("ExitCode"),
                "restart_count": record.get("RestartCount"),
                "oom_killed": bool(state.get("OOMKilled")),
                "error_present": bool(state.get("Error")),
                "health": (state.get("Health") or {}).get("Status"),
            }
        return states, records_by_service

    try:
        acceptance_success(
            runtime.compose("config", "--quiet"),
            "The receiver health component Compose config is invalid",
        )
        config = acceptance_json(
            runtime.compose(
                "--profile",
                "operations",
                "--profile",
                "seed",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ),
            "The receiver health component configuration could not be inspected",
        )
        service_config = config.get("services", {})
        runtime.stack.require_service_image_config(service_config)
        if {
            name
            for name, service in service_config.items()
            if isinstance(service, dict) and service.get("ports")
        } != {"gateway"}:
            raise AcceptanceFailure("A telemetry component unexpectedly publishes a host port")

        evidence["stage"] = "build_exact_source_images"
        acceptance_success(
            runtime.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={runtime.stack.expected_sha}",
                "api",
                "collector",
                timeout=900,
            ),
            "The receiver health component images could not be built",
        )
        expected_images = {
            "backend": f"prl-backend:{runtime.stack.image_tag}",
            "collector": f"prl-collector:{runtime.stack.image_tag}",
        }
        for role, image in expected_images.items():
            records = acceptance_json(
                acceptance_invoke(["docker", "image", "inspect", image]),
                f"The {role} component image provenance could not be inspected",
            )
            labels = (records[0].get("Config") or {}).get("Labels") or {}
            if labels.get("org.opencontainers.image.revision") != runtime.stack.expected_sha:
                raise AcceptanceFailure(
                    f"The {role} component image is not labeled with this source"
                )

        evidence["stage"] = "start_backend_receiver_only"
        acceptance_success(
            runtime.compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
                *services,
                timeout=240,
            ),
            "The backend and receiver component services did not start",
        )
        service_states, service_records = safe_service_snapshot()
        receiver_record = service_records.get("local_alert_receiver")
        if any(
            item["status"] != "running" or item["restart_count"] != 0 or item["oom_killed"]
            for item in service_states.values()
        ):
            evidence["service_states"] = service_states
            raise AcceptanceFailure("A backend component was not stably running")
        if receiver_record is None:
            raise AcceptanceFailure("The UUID-owned receiver container is missing")
        receiver_id = receiver_record.get("Id")
        if not isinstance(receiver_id, str) or not re.fullmatch(r"[0-9a-f]{64}", receiver_id):
            raise AcceptanceFailure("The UUID-owned receiver identity is invalid")
        if (receiver_record.get("Config") or {}).get("Tty") is not False:
            raise AcceptanceFailure("The receiver diagnostic requires a non-TTY container")

        evidence["stage"] = "probe_receiver_network_namespace"
        try:
            probe = acceptance_invoke(
                [
                    "docker",
                    "exec",
                    receiver_id,
                    "python",
                    "-c",
                    RECEIVER_COMPONENT_PROBE_SCRIPT,
                    runtime.ops_host,
                ],
                timeout=90,
            )
        except (OSError, subprocess.SubprocessError):
            probe = None
        if probe is None or probe.returncode:
            evidence["probe_error_category"] = "subprocess_error"
            evidence["service_states"] = service_states
            print(
                "TASK6_RECEIVER_COMPONENT_EVIDENCE="
                + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            )
            raise AcceptanceFailure("The bounded receiver backend probe could not run")
        try:
            raw_probe = json.loads(probe.stdout)
        except json.JSONDecodeError:
            evidence["probe_error_category"] = "invalid_envelope"
            evidence["service_states"] = service_states
            print(
                "TASK6_RECEIVER_COMPONENT_EVIDENCE="
                + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            )
            raise AcceptanceFailure(
                "The receiver backend probe returned an invalid summary"
            ) from None
        summary = safe_receiver_component_summary(raw_probe)
        evidence["probe"] = summary
        evidence["service_states"] = service_states
        print(
            "TASK6_RECEIVER_COMPONENT_EVIDENCE="
            + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        )

        targets = summary["targets"]
        if (
            summary["poll_healthy"] is not True
            or summary["poll_unhealthy"] != []
            or summary["unknown_poll_failure_count"] != 0
            or summary["target_count"] != len(RECEIVER_HEALTH_TARGETS)
            or summary["unknown_target_count"] != 0
            or set(targets) != RECEIVER_HEALTH_TARGETS
        ):
            raise AcceptanceFailure(
                "The receiver did not report all finite telemetry targets healthy"
            )
        health = summary["receiver_health"]
        if not receiver_health_is_passed(health):
            raise AcceptanceFailure("The receiver self-health endpoint did not pass")
        if any(
            target.get("initial_status") != 200
            or target.get("redirect")
            or target.get("final_status") != 200
            or target.get("follow_error_category") is not None
            for target in targets.values()
        ):
            raise AcceptanceFailure("A telemetry backend readiness target did not return HTTP 200")
        passed = True
    finally:
        if not passed:
            try:
                states, _records = safe_service_snapshot()
                evidence["pre_cleanup_service_states"] = states
            except Exception:
                evidence["pre_cleanup_snapshot_available"] = False
            print(
                "TASK6_RECEIVER_COMPONENT_FAILURE_SNAPSHOT:"
                + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            )
        runtime.cleanup()


def _object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _validated_epoch_seconds(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    timestamp = float(value)
    if not math.isfinite(timestamp) or not 0 < timestamp < 10_000_000_000:
        return None
    return timestamp


def post_restart_container_started_epoch(
    record: object,
    *,
    expected_container_id: str,
    expected_project: str,
    later_than_epoch: object | None = None,
) -> float | None:
    """Return a finite start time for the same running Compose-owned container generation."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_container_id) or not expected_project:
        return None
    container = _object_mapping(record)
    config = _object_mapping(container.get("Config"))
    labels = _object_mapping(config.get("Labels"))
    state = _object_mapping(container.get("State"))
    if (
        container.get("Id") != expected_container_id
        or labels.get("com.docker.compose.project") != expected_project
        or state.get("Status") != "running"
    ):
        return None

    raw_started_at = state.get("StartedAt")
    if not isinstance(raw_started_at, str):
        return None
    try:
        started_at = datetime.fromisoformat(raw_started_at.replace("Z", "+00:00"))
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            return None
        timestamp = _validated_epoch_seconds(started_at.timestamp())
    except (OSError, OverflowError, ValueError):
        return None
    if timestamp is None:
        return None

    if later_than_epoch is not None:
        previous_timestamp = _validated_epoch_seconds(later_than_epoch)
        if previous_timestamp is None or timestamp <= previous_timestamp:
            return None
    return timestamp


def _finite_prometheus_number(value: object) -> float | None:
    if type(value) in (int, float):
        number = float(value)
    elif isinstance(value, str) and len(value) <= 64:
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
    else:
        return None
    return number if math.isfinite(number) and abs(number) < 10_000_000_000 else None


def prometheus_up_timestamp_query(job: str) -> str:
    """Build a query that keeps only up targets and returns each stored sample time."""
    return f'timestamp(up{{job="{job}"}}) and on(job, instance) (up{{job="{job}"}} == 1)'


def prometheus_timestamp_query_sample_after_epoch(
    samples: object,
    *,
    not_before_epoch: object,
    job: str | None = None,
) -> float | None:
    """Return value[1] from a timestamp() vector when its stored sample is after an epoch.

    Instant-query values are ``[evaluation timestamp, stored sample timestamp]``; value[0]
    identifies when Prometheus evaluated the query and cannot establish sample freshness.
    """
    minimum_epoch = _validated_epoch_seconds(not_before_epoch)
    if minimum_epoch is None or not isinstance(samples, list):
        return None
    for sample in samples:
        sample_mapping = _object_mapping(sample)
        labels = _object_mapping(sample_mapping.get("metric"))
        values = sample_mapping.get("value")
        if (
            (job is not None and labels.get("job") != job)
            or not isinstance(values, list)
            or len(values) != 2
        ):
            continue
        evaluation_epoch = _finite_prometheus_number(values[0])
        stored_sample_epoch = _finite_prometheus_number(values[1])
        if (
            evaluation_epoch is not None
            and stored_sample_epoch is not None
            and stored_sample_epoch >= minimum_epoch
        ):
            return stored_sample_epoch
    return None


def prometheus_timestamp_query_has_sample_after_epoch(
    samples: object,
    *,
    not_before_epoch: object,
    job: str | None = None,
) -> bool:
    """Report whether a timestamp() vector contains a fresh stored sample."""
    return (
        prometheus_timestamp_query_sample_after_epoch(
            samples,
            not_before_epoch=not_before_epoch,
            job=job,
        )
        is not None
    )


def worker_heartbeat_missing_failure_diagnostics(
    barriers: object,
    predicate: object,
) -> dict[str, bool | int]:
    """Keep pytest failure details to bounded booleans/counts without Prometheus labels."""
    barrier_values = _object_mapping(barriers)
    collector = _object_mapping(barrier_values.get("collector"))
    application_otel = _object_mapping(barrier_values.get("application_otel"))
    predicate_values = _object_mapping(predicate)
    diagnostic: dict[str, bool | int] = {
        "collector_scrape_observed": collector.get("collector_target_up") is True,
        "application_otel_scrape_observed": (
            application_otel.get("application_otel_target_up") is True
        ),
    }
    for key in (
        "expected_slot_sample_count",
        "current_heartbeat_sample_count",
        "raw_heartbeat_sample_count",
    ):
        count = predicate_values.get(key)
        if type(count) is int and 0 <= count <= 1_000_000:
            diagnostic[key] = count
    for key in (
        "raw_heartbeat_predates_worker_exit",
        "current_heartbeat_recorded_after_collector_restart",
    ):
        value = predicate_values.get(key)
        if isinstance(value, bool):
            diagnostic[key] = value
    return diagnostic


def format_worker_heartbeat_missing_failure_message(
    reason: str,
    diagnostics: dict[str, bool | int],
) -> str:
    return (
        reason
        + "; bounded_observations="
        + json.dumps(diagnostics, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def _worker_metric_value(state: dict[str, dict[str, object]], role: str) -> float | None:
    sample = state.get(role)
    if not isinstance(sample, dict):
        return None
    value = sample.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


@pytest.mark.skipif(
    not _full_stack_enabled(),
    reason="requires all five opt-in Compose runtime gates",
)
def test_full_observability_stack_runtime_acceptance(tmp_path: Path) -> None:
    run_started_monotonic = time.monotonic()
    run_started_at = datetime.now(UTC)
    run_id = run_started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_directory = ROOT / "build/observability-review" / f"run-{run_id}"
    screenshot_root = run_directory / "screenshots"
    config_hashes = collect_config_hashes(ROOT)
    try:
        checkout_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        checkout_sha = checkout_result.stdout.strip() if checkout_result.returncode == 0 else None
    except Exception:
        checkout_sha = None
    if not isinstance(checkout_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", checkout_sha):
        checkout_sha = None
    pr_head_sha = os.environ.get("PR_HEAD_SHA")
    if not isinstance(pr_head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", pr_head_sha):
        pr_head_sha = None
    runtime_source_sha: str | None = checkout_sha
    screenshot_evidence: dict[str, object] | None = None
    evidence: dict[str, object] = {}
    worker_alert_evidence: dict[str, object] = {}
    failure_context: dict[str, object] = {"stage": "runtime_start"}

    def persist_review_evidence(outcome: str, error_type: str | None, stage: object) -> None:
        finished_at = datetime.now(UTC)
        elapsed = max(0.0, time.monotonic() - run_started_monotonic)
        runtime_document = build_runtime_evidence(
            outcome=outcome,
            checkout_sha=checkout_sha,
            source_sha=runtime_source_sha,
            pr_head_sha=pr_head_sha,
            started_at_utc=run_started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            finished_at_utc=finished_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            elapsed_seconds=elapsed,
            config_sha256=config_hashes,
            evidence=evidence,
            worker_alerts=worker_alert_evidence,
            screenshot_evidence=screenshot_evidence,
        )
        failure_document = build_failure_diagnostics(
            outcome=outcome,
            checkout_sha=checkout_sha,
            source_sha=runtime_source_sha,
            pr_head_sha=pr_head_sha,
            started_at_utc=run_started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            elapsed_seconds=elapsed,
            config_sha256=config_hashes,
            stage=stage,
            error_type=error_type,
            evidence=evidence,
            worker_alerts=worker_alert_evidence,
            failure_context=failure_context,
            screenshot_evidence=screenshot_evidence,
        )
        runtime_path, failure_path = write_evidence_documents(
            run_directory,
            runtime_document=runtime_document,
            failure_document=failure_document,
        )
        print(f"TASK6_RUNTIME_EVIDENCE_ARTIFACT={runtime_path.relative_to(ROOT).as_posix()}")
        print(f"TASK6_FAILURE_DIAGNOSTICS_ARTIFACT={failure_path.relative_to(ROOT).as_posix()}")

    try:
        runtime = FullStackRuntime(tmp_path, pin_ops_edge_network=True)
        runtime_source_sha = runtime.stack.expected_sha
    except Exception as exc:
        failure_context["stage"] = "runtime_initialization"
        try:
            persist_review_evidence("failed", type(exc).__name__, "runtime_initialization")
        except Exception:
            print("TASK6_EVIDENCE_WRITE_FAILURE=io_error")
        raise

    sink_observations: dict[str, dict[str, object]] = {}
    failure_context.update(
        {
            "stage": "runtime_start",
            "route_status": None,
            "request_id": "",
            "trace_id": "",
            "worker_restart_observations": {"available": False},
            "worker_alerts": worker_alert_evidence,
        }
    )
    accepted = False

    try:
        worker_alert_rules_document = yaml.safe_load(
            (ROOT / "tools/infra/prometheus-rules.yml").read_text(encoding="utf-8")
        )
        worker_alert_expressions = {
            rule["alert"]: rule["expr"]
            for group in worker_alert_rules_document["groups"]
            for rule in group["rules"]
            if rule.get("alert")
            in {
                "WorkerHeartbeatStale",
                "WorkerHeartbeatMissing",
                "WorkerProgressStale",
                "WorkerBacklogSnapshotStale",
                "WorkerBacklogOverdue",
            }
        }
    except Exception as exc:
        failure_context["stage"] = "worker_alert_rule_load"
        try:
            runtime.cleanup()
        except Exception:
            pass
        try:
            persist_review_evidence("failed", type(exc).__name__, "worker_alert_rule_load")
        except Exception:
            print("TASK6_EVIDENCE_WRITE_FAILURE=io_error")
        raise
    worker_alert_severity = {
        "WorkerHeartbeatStale": "critical",
        "WorkerHeartbeatMissing": "critical",
        "WorkerProgressStale": "critical",
        "WorkerBacklogSnapshotStale": "warning",
        "WorkerBacklogOverdue": "warning",
    }
    worker_alert_slot = {
        "WorkerHeartbeatStale": "1",
        "WorkerHeartbeatMissing": "1",
        "WorkerProgressStale": "1",
        "WorkerBacklogSnapshotStale": "1",
        "WorkerBacklogOverdue": None,
    }

    def prometheus_vector(expression: str, deadline: float) -> list[dict[str, object]]:
        response = runtime.api_json(
            "http://prometheus:9090/api/v1/query?" + urlencode({"query": expression}),
            deadline=deadline,
        )
        if not isinstance(response, dict) or response.get("status") != "success":
            raise AcceptanceFailure("Prometheus returned an unsuccessful worker alert query")
        data = response.get("data")
        raw_samples = data.get("result") if isinstance(data, dict) else None
        if not isinstance(raw_samples, list) or len(raw_samples) > 100:
            raise AcceptanceFailure("Prometheus returned an invalid worker alert vector")
        samples = [sample for sample in raw_samples if isinstance(sample, dict)]
        if len(samples) != len(raw_samples):
            raise AcceptanceFailure("Prometheus returned a malformed worker alert sample")
        return samples

    def worker_alert_samples(
        samples: list[dict[str, object]], *, worker_slot: str | None
    ) -> list[dict[str, object]]:
        matches = []
        for sample in samples:
            labels = sample.get("metric")
            if not isinstance(labels, dict):
                raise AcceptanceFailure("Prometheus returned missing worker alert labels")
            if (
                labels.get("service_name") != "worker"
                or labels.get("deployment_environment_name") != "demo"
            ):
                continue
            if worker_slot is None:
                if "worker_slot" in labels:
                    raise AcceptanceFailure(
                        "The overdue worker alert predicate gained a slot label"
                    )
            elif labels.get("worker_slot") != worker_slot:
                continue
            matches.append(sample)
        return matches

    def worker_receiver_snapshot(alertname: str) -> list[dict[str, str]]:
        return runtime.alert_receiver_statuses(alertname)

    def matching_receiver_episode(
        alertname: str,
        *,
        status: str,
        baseline: list[dict[str, str]],
        identity: dict[str, object],
        deadline: float,
    ) -> dict[str, str] | None:
        current = runtime.alert_receiver_statuses(alertname, deadline=deadline)
        fresh = new_alert_statuses_since_snapshot(
            baseline,
            current,
            status=status,
            severity=worker_alert_severity[alertname],
        )
        for record in fresh:
            if record.get("fingerprint") == identity.get("fingerprint") and record.get(
                "starts_at"
            ) == identity.get("starts_at"):
                return record
        return None

    def wait_for_worker_alert_firing(
        alertname: str,
        baseline: list[dict[str, str]],
        deadline: MonotonicDeadline,
        stimulus_check: Callable[[float], object] | None = None,
    ) -> dict[str, object]:
        poll_count = 0
        severity = worker_alert_severity[alertname]
        worker_slot = worker_alert_slot[alertname]
        expression = worker_alert_expressions[alertname]

        def probe(request_deadline: float) -> object:
            nonlocal poll_count
            poll_count += 1
            stimulus_observation = (
                stimulus_check(request_deadline) if stimulus_check is not None else None
            )
            if stimulus_check is not None and stimulus_observation is None:
                return None
            predicate_samples = worker_alert_samples(
                prometheus_vector(expression, request_deadline), worker_slot=worker_slot
            )
            firing_samples = worker_alert_samples(
                prometheus_vector(
                    f'ALERTS{{alertname="{alertname}",alertstate="firing"}}',
                    request_deadline,
                ),
                worker_slot=worker_slot,
            )
            if len(predicate_samples) > 1 or len(firing_samples) > 1:
                raise AcceptanceFailure("Prometheus returned multiple matching worker alert series")
            if not predicate_samples or not firing_samples:
                return None
            active = active_worker_alert_identity(
                runtime.api_json(
                    "http://alertmanager:9093/api/v2/alerts", deadline=request_deadline
                ),
                alertname,
                severity=severity,
                worker_slot=worker_slot,
            )
            if active is None:
                return None
            predicate_labels = _object_mapping(predicate_samples[0].get("metric"))
            firing_labels = _object_mapping(firing_samples[0].get("metric"))
            active_labels = _object_mapping(active.get("labels"))
            common_labels = ("service_name", "deployment_environment_name")
            if any(
                predicate_labels.get(label) != firing_labels.get(label)
                or firing_labels.get(label) != active_labels.get(label)
                for label in common_labels
            ):
                return None
            if (
                firing_labels.get("alertname") != alertname
                or firing_labels.get("alertstate") != "firing"
                or firing_labels.get("severity") != severity
            ):
                return None
            if worker_slot is None:
                if any(
                    "worker_slot" in labels
                    for labels in (predicate_labels, firing_labels, active_labels)
                ):
                    raise AcceptanceFailure(
                        "The backlog overdue firing identity unexpectedly had a slot"
                    )
            elif any(
                labels.get("worker_slot") != worker_slot
                for labels in (predicate_labels, firing_labels, active_labels)
            ):
                return None
            delivery = matching_receiver_episode(
                alertname,
                status="firing",
                baseline=baseline,
                identity=active,
                deadline=request_deadline,
            )
            if delivery is None:
                return None
            return {
                **active,
                "receiver_delivery": delivery,
                "prometheus_predicate_sample_count": len(predicate_samples),
                "prometheus_firing_sample_count": len(firing_samples),
                "poll_count": poll_count,
                "detection_elapsed_seconds": round(deadline.elapsed, 3),
                **(
                    {"stimulus_observation": stimulus_observation}
                    if stimulus_check is not None
                    else {}
                ),
            }

        result = wait_until_deadline(
            deadline,
            probe,
            failure=f"The real {alertname} firing notification did not arrive before its deadline",
            interval_seconds=5,
        )
        if not isinstance(result, dict):
            raise AcceptanceFailure("The worker alert firing observation was malformed")
        return result

    def wait_for_worker_alert_resolution(
        alertname: str,
        firing: dict[str, object],
        baseline: list[dict[str, str]],
        deadline: MonotonicDeadline,
        recovery_check: Callable[[float], bool],
    ) -> dict[str, object]:
        poll_count = 0
        severity = worker_alert_severity[alertname]
        worker_slot = worker_alert_slot[alertname]
        expression = worker_alert_expressions[alertname]

        def probe(request_deadline: float) -> object:
            nonlocal poll_count
            poll_count += 1
            predicate_samples = worker_alert_samples(
                prometheus_vector(expression, request_deadline), worker_slot=worker_slot
            )
            firing_samples = worker_alert_samples(
                prometheus_vector(
                    f'ALERTS{{alertname="{alertname}",alertstate="firing"}}',
                    request_deadline,
                ),
                worker_slot=worker_slot,
            )
            if predicate_samples or firing_samples or not recovery_check(request_deadline):
                return None
            active = active_worker_alert_identity(
                runtime.api_json(
                    "http://alertmanager:9093/api/v2/alerts", deadline=request_deadline
                ),
                alertname,
                severity=severity,
                worker_slot=worker_slot,
            )
            if active is not None:
                return None
            identity = {
                "fingerprint": firing.get("fingerprint"),
                "starts_at": firing.get("starts_at"),
            }
            delivery = matching_receiver_episode(
                alertname,
                status="resolved",
                baseline=baseline,
                identity=identity,
                deadline=request_deadline,
            )
            if delivery is None:
                return None
            firing_delivery = firing.get("receiver_delivery")
            if not isinstance(firing_delivery, dict):
                raise AcceptanceFailure("The worker firing delivery omitted its receiver timestamp")
            try:
                received = normalize_alertmanager_timestamp(delivery.get("received_at"))
                fired = normalize_alertmanager_timestamp(firing_delivery.get("received_at"))
            except ValueError:
                raise AcceptanceFailure("A worker alert receiver timestamp was invalid") from None
            if datetime.fromisoformat(received.replace("Z", "+00:00")) <= datetime.fromisoformat(
                fired.replace("Z", "+00:00")
            ):
                return None
            return {
                "fingerprint": identity["fingerprint"],
                "starts_at": identity["starts_at"],
                "receiver_delivery": delivery,
                "prometheus_predicate_cleared": True,
                "prometheus_firing_cleared": True,
                "real_recovery_observed": True,
                "poll_count": poll_count,
                "recovery_elapsed_seconds": round(deadline.elapsed, 3),
            }

        result = wait_until_deadline(
            deadline,
            probe,
            failure=(
                f"The real {alertname} resolved notification did not arrive before its deadline"
            ),
            interval_seconds=5,
        )
        if not isinstance(result, dict):
            raise AcceptanceFailure("The worker alert recovery observation was malformed")
        return result

    try:
        failure_context["stage"] = "stack_start_and_self_health"
        runtime.start()
        evidence["project"] = runtime.stack.project
        evidence["source_sha"] = runtime.stack.expected_sha
        gateway = runtime.inspect_service("gateway")
        raw_port_bindings = _object_mapping(gateway.get("HostConfig")).get("PortBindings")
        port_bindings = _object_mapping(raw_port_bindings)
        raw_ops_binding = port_bindings.get("8443/tcp")
        ops_bindings = raw_ops_binding if isinstance(raw_ops_binding, list) else []
        if len(ops_bindings) != 1 or not isinstance(ops_bindings[0], dict):
            raise AcceptanceFailure("The ops listener is not published on one loopback port")
        ops_binding = _object_mapping(ops_bindings[0])
        if ops_binding.get("HostIp") != "127.0.0.1" or ops_binding.get("HostPort") != str(
            runtime.port
        ):
            raise AcceptanceFailure("The ops listener is not bound to its UUID-owned loopback port")
        evidence["ops_host_binding"] = {"host_ip": "127.0.0.1", "host_port": runtime.port}
        evidence["target_matrix"] = runtime.wait_targets()
        failure_context["stage"] = "gateway_sli_queries_against_pinned_loki"
        expected_backend_image = "prl-backend:" + runtime.stack.image_tag
        expected_frontend_image = "prl-frontend:" + runtime.stack.image_tag
        api_config = _object_mapping(runtime.inspect_service("api").get("Config"))
        worker_config = _object_mapping(runtime.inspect_service("worker").get("Config"))
        frontend_config = _object_mapping(runtime.inspect_service("frontend").get("Config"))
        if (
            api_config.get("Image") != expected_backend_image
            or worker_config.get("Image") != expected_backend_image
            or frontend_config.get("Image") != expected_frontend_image
        ):
            raise AcceptanceFailure("Derived app images do not use the UUID-owned tag")
        evidence["derived_images"] = {
            "backend": expected_backend_image,
            "frontend": expected_frontend_image,
        }
        expected_caps = {
            "mysql": (1024, 1_000_000_000, 256),
            "api": (512, 1_000_000_000, 128),
            "worker": (512, 1_000_000_000, 128),
            "frontend": (512, 1_000_000_000, 128),
            "gateway": (128, 500_000_000, 64),
            "collector": (512, 1_000_000_000, 128),
            "prometheus": (512, 1_000_000_000, 128),
            "loki": (768, 1_000_000_000, 128),
            "tempo": (512, 1_000_000_000, 128),
            "grafana": (512, 1_000_000_000, 128),
            "alertmanager": (128, 500_000_000, 64),
            "local_alert_receiver": (128, 500_000_000, 64),
            "gateway_log_rotate": (128, 250_000_000, 32),
        }
        actual_caps = {}
        for service, (memory_mib, nano_cpus, pids_limit) in expected_caps.items():
            host = _object_mapping(runtime.inspect_service(service).get("HostConfig"))
            actual = (host.get("Memory"), host.get("NanoCpus"), host.get("PidsLimit"))
            expected = (memory_mib * 1024 * 1024, nano_cpus, pids_limit)
            if actual != expected:
                raise AcceptanceFailure(f"The UUID-owned {service} resource caps changed")
            actual_caps[service] = {
                "memory_mib": memory_mib,
                "nano_cpus": nano_cpus,
                "pids_limit": pids_limit,
            }
        evidence["steady_resource_caps"] = actual_caps

        failure_context["stage"] = "application_https_readiness"
        app_status, headers, body = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if app_status != 200 or json.loads(body).get("status") != "ready":
            raise AcceptanceFailure("Verified application HTTPS readiness failed")
        lower_headers = {name.lower(): value for name, value in headers.items()}
        request_id = lower_headers.get("x-request-id", "")
        trace_id = lower_headers.get("x-trace-id", "")
        failure_context["route_status"] = app_status
        failure_context["request_id"] = request_id
        failure_context["trace_id"] = trace_id
        if not re.fullmatch(r"[a-f0-9]{32}", request_id) or not re.fullmatch(
            r"[a-f0-9]{32}", trace_id
        ):
            raise AcceptanceFailure("The real HTTPS request omitted safe trace correlation")
        gateway_event: dict[str, object] = {}

        def observe_boundary(name: str, operation) -> None:
            try:
                sink_observations[name] = operation()
            except Exception as exc:
                sink_observations[name] = {"failure_category": type(exc).__name__}

        def signals_stored() -> bool:
            observe_boundary(
                "loki_query",
                lambda: runtime.observe_loki_request(request_id, trace_id),
            )
            observe_boundary(
                "loki_index_series",
                lambda: runtime.observe_loki_index_series(expected_request_id=request_id),
            )
            observe_boundary(
                "api_native_log",
                lambda: runtime.observe_loki_native_application_log("api", "http.server.request"),
            )
            observe_boundary(
                "worker_native_log",
                lambda: runtime.observe_loki_native_application_log("worker", "worker.heartbeat"),
            )
            observe_boundary(
                "tempo",
                lambda: runtime.observe_tempo_trace(trace_id),
            )
            return all(
                sink_observations[name].get("failure_category") is None
                for name in (
                    "loki_query",
                    "loki_index_series",
                    "api_native_log",
                    "worker_native_log",
                    "tempo",
                )
            )

        failure_context["stage"] = "native_logs_loki_query_index_and_tempo_delivery"
        runtime.stack.wait_for(
            signals_stored,
            seconds=45,
            failure=(
                "The verified application/API and worker logs plus API trace "
                "did not reach Loki and Tempo"
            ),
        )
        gateway_event = runtime.query_loki_request(request_id, trace_id)
        evidence["gateway_loki_event"] = gateway_event
        evidence["tempo_trace"] = "stored"
        from tests.containers.test_observability_e2e import (
            LiveBusinessProbe,
            api_telemetry_loss_totals,
            append_stale_gateway_event,
            capture_dashboard_screenshots,
            run_backend_outage,
            verify_stale_gateway_event,
        )

        business_probe = LiveBusinessProbe(runtime)
        failure_context["stage"] = "healthy_business_acceptance"
        healthy_business, healthy_trace = business_probe.run_case("healthy")
        business_matrix: dict[str, object] = {"healthy": healthy_business}
        evidence["business_outage_matrix"] = business_matrix
        failure_context["stage"] = "business_trace_correlation"
        failure_context["request_id"] = healthy_trace["request_id"]
        failure_context["trace_id"] = healthy_trace["trace_id"]
        evidence["business_trace_correlation"] = business_probe.verify_trace_correlation(
            healthy_trace
        )
        evidence["native_otlp_logs"] = {
            "api": sink_observations["api_native_log"],
            "worker": sink_observations["worker_native_log"],
        }

        failure_context["stage"] = "collector_stop_api_readiness"
        pipeline_rules = yaml.safe_load(
            (ROOT / "tools/infra/loki-rules/fake/gateway-sli.yml").read_text(encoding="utf-8")
        )
        gateway_recording_rules = {
            rule["record"]: rule
            for group in pipeline_rules["groups"]
            for rule in group["rules"]
            if "record" in rule
        }
        gateway_alert_rules = {
            rule["alert"]: rule
            for group in pipeline_rules["groups"]
            for rule in group["rules"]
            if "alert" in rule
        }
        gateway_dashboard = json.loads(
            (ROOT / "tools/infra/grafana/dashboards/gateway.json").read_text(encoding="utf-8")
        )
        gateway_dashboard_panels = {panel["id"]: panel for panel in gateway_dashboard["panels"]}
        gateway_sli_query_expressions = {
            "dashboard_availability": gateway_dashboard_panels[2]["targets"][0]["expr"],
            "dashboard_fast_success": gateway_dashboard_panels[3]["targets"][0]["expr"],
            "yaml_availability": gateway_recording_rules["prl:gateway_availability:ratio5m"][
                "expr"
            ],
            "yaml_fast_success": gateway_recording_rules["prl:gateway_fast_success:ratio5m"][
                "expr"
            ],
            "yaml_fast_success_alert": gateway_alert_rules["GatewayFastSuccessBelowSLO"]["expr"],
        }
        gateway_sli_ratio_names = (
            "dashboard_availability",
            "dashboard_fast_success",
            "yaml_availability",
            "yaml_fast_success",
        )
        gateway_request_denominators = {
            "availability": """
                sum(count_over_time(
                  {service_name="gateway",deployment_environment_name="demo"}
                  | json event="event", classification="classification", route="route"
                  | __error__ = ""
                  | event="gateway.request"
                  | classification=~"success|service_failure"
                  | keep event, classification, route
                  [5m]
                ))
            """,
            "successful_requests": """
                sum(count_over_time(
                  {service_name="gateway",deployment_environment_name="demo"}
                  | json event="event", classification="classification", route="route"
                  | __error__ = ""
                  | event="gateway.request"
                  | classification="success"
                  | keep event, classification, route
                  [5m]
                ))
            """,
        }

        def query_gateway_sli_samples(expressions: dict[str, str]) -> dict[str, float | None]:
            return {
                name: runtime.loki_scalar(expression) for name, expression in expressions.items()
            }

        pipeline_rule = next(
            rule
            for group in pipeline_rules["groups"]
            for rule in group["rules"]
            if rule.get("alert") == "GatewayPipelineHeartbeatMissing"
        )
        pipeline_missing_expression = pipeline_rule["expr"]
        if (
            "absent_over_time" not in pipeline_missing_expression
            or "[60s]" not in pipeline_missing_expression
            or pipeline_rule.get("for") != "30s"
        ):
            raise AcceptanceFailure("The gateway pipeline absence rule changed its approved timing")
        pre_outage_healthchecks = runtime.query_loki_healthchecks()
        latest_pre_outage_event = pre_outage_healthchecks.get("latest_event_unix_ns")
        if type(latest_pre_outage_event) is not int:
            raise AcceptanceFailure(
                "The genuine gateway healthcheck was not fresh before Collector pause"
            )
        if time.time_ns() - latest_pre_outage_event > 15_000_000_000:
            raise AcceptanceFailure(
                "The genuine gateway healthcheck was not fresh before Collector pause"
            )
        evidence["genuine_gateway_healthchecks_before_collector_outage"] = pre_outage_healthchecks
        gateway_sli_samples_before_collector_stop = query_gateway_sli_samples(
            gateway_sli_query_expressions
        )
        gateway_request_denominators_before_collector_stop = query_gateway_sli_samples(
            gateway_request_denominators
        )
        if any(
            gateway_sli_samples_before_collector_stop[name] is None
            for name in gateway_sli_ratio_names
        ) or any(
            value is None or value <= 0
            for value in gateway_request_denominators_before_collector_stop.values()
        ):
            raise AcceptanceFailure(
                "Fresh genuine gateway healthchecks and positive request denominators "
                "were not queryable before the Collector stop"
            )
        evidence["gateway_sli_samples_before_collector_stop"] = {
            "sample_values": gateway_sli_samples_before_collector_stop,
            "request_denominators": gateway_request_denominators_before_collector_stop,
            "dashboard_queries_are_exact_provisioned_expressions": True,
            "yaml_queries_are_exact_rule_expressions_not_recording_series": True,
        }
        collector_before = runtime.inspect_service("collector")
        collector_id_before = collector_before.get("Id")
        if not isinstance(collector_id_before, str) or not re.fullmatch(
            r"[0-9a-f]{64}", collector_id_before
        ):
            raise AcceptanceFailure("The UUID-owned Collector identity is invalid before outage")
        collector_loss_before = api_telemetry_loss_totals(runtime)
        collector_outage_started_ns = time.time_ns()
        acceptance_success(
            runtime.compose("stop", "--timeout", "10", "collector", timeout=30),
            "The UUID-owned Collector could not stop for API readiness isolation",
        )
        stopped_records = acceptance_json(
            acceptance_invoke(["docker", "inspect", collector_id_before]),
            "The UUID-owned stopped Collector could not be inspected",
        )
        if (
            len(stopped_records) != 1
            or stopped_records[0].get("Id") != collector_id_before
            or (stopped_records[0].get("Config", {}).get("Labels") or {}).get(
                "com.docker.compose.project"
            )
            != runtime.stack.project
            or (stopped_records[0].get("State") or {}).get("Status") != "exited"
        ):
            raise AcceptanceFailure("The UUID-owned Collector did not reach stopped state")
        readiness = summarize_api_readiness_observation(
            runtime.api_json_observation("http://127.0.0.1:8000/health/ready")
        )
        failure_context["collector_outage_readiness"] = readiness
        if not readiness["passed"]:
            raise AcceptanceFailure(
                "Application/API readiness depended on Collector availability: "
                + json.dumps(readiness, sort_keys=True, separators=(",", ":"))
            )
        runtime.wait_app_gateway()
        evidence["collector_outage_readiness"] = readiness
        evidence["gateway_ready_while_collector_stopped"] = True

        failure_context["stage"] = "collector_pipeline_missing_predicate"
        pipeline_predicate: float | None = None

        def pipeline_missing_predicate_visible() -> bool:
            nonlocal pipeline_predicate
            pipeline_predicate = runtime.loki_scalar(pipeline_missing_expression)
            return pipeline_predicate is not None and pipeline_predicate > 0

        # Compose allows a 10-second graceful stop. A genuine event may arrive during that
        # interval, so allow 10 + the 60-second absence window + 10 seconds for polling.
        runtime.stack.wait_for(
            pipeline_missing_predicate_visible,
            seconds=80,
            failure="The pinned Loki absence predicate did not become visible after 60 seconds",
        )
        gateway_request_denominators_at_heartbeat_expiry = query_gateway_sli_samples(
            gateway_request_denominators
        )
        if any(
            value is None or value <= 0
            for value in gateway_request_denominators_at_heartbeat_expiry.values()
        ):
            raise AcceptanceFailure(
                "The prior five-minute gateway request denominators expired with the heartbeat"
            )
        gateway_sli_samples_after_heartbeat_expiry = query_gateway_sli_samples(
            gateway_sli_query_expressions
        )
        if any(
            gateway_sli_samples_after_heartbeat_expiry[name] is not None
            for name in (*gateway_sli_ratio_names, "yaml_fast_success_alert")
        ):
            raise AcceptanceFailure(
                "Gateway ratios or the fast-success alert predicate remained queryable "
                "after genuine heartbeat expiry"
            )
        evidence["gateway_request_denominators_at_heartbeat_expiry"] = (
            gateway_request_denominators_at_heartbeat_expiry
        )
        evidence["gateway_sli_samples_after_heartbeat_expiry"] = {
            "sample_values": gateway_sli_samples_after_heartbeat_expiry,
            "ratio_samples_absent": True,
            "fast_success_alert_predicate_absent": True,
        }
        evidence["collector_pipeline_missing_predicate"] = {
            "expression": pipeline_missing_expression,
            "value": pipeline_predicate,
            "collector_outage_started_unix_ns": collector_outage_started_ns,
        }

        failure_context["stage"] = "collector_pipeline_missing_firing"
        pipeline_firing_observed_ns: int | None = None

        def pipeline_missing_firing_received() -> bool:
            nonlocal pipeline_firing_observed_ns
            received = any(
                item["status"] == "firing" and item["severity"] == "critical"
                for item in runtime.alert_receiver_statuses("GatewayPipelineHeartbeatMissing")
            )
            if received and pipeline_firing_observed_ns is None:
                pipeline_firing_observed_ns = time.time_ns()
            return received

        # Bound: the configured 30-second hold, two 15-second evaluations and 5-second wait.
        runtime.stack.wait_for(
            pipeline_missing_firing_received,
            seconds=70,
            failure="The local receiver did not receive GatewayPipelineHeartbeatMissing",
        )
        evidence["collector_pipeline_missing_firing"] = {
            "alertname": "GatewayPipelineHeartbeatMissing",
            "receiver_statuses": runtime.alert_receiver_statuses("GatewayPipelineHeartbeatMissing"),
            "receiver_first_observed_unix_ns": pipeline_firing_observed_ns,
            "exact_predicate_value": runtime.loki_scalar(pipeline_missing_expression),
        }

        collector_warning_firing_ns: int | None = None

        def collector_warning_firing_received() -> bool:
            nonlocal collector_warning_firing_ns
            received = any(
                item["status"] == "firing" and item["severity"] == "warning"
                for item in runtime.alert_receiver_statuses("TelemetryTargetDown")
            )
            if received and collector_warning_firing_ns is None:
                collector_warning_firing_ns = time.time_ns()
            return received

        runtime.stack.wait_for(
            collector_warning_firing_received,
            seconds=45,
            failure="The consolidated Collector target-down warning did not reach the receiver",
        )
        if runtime.alert_receiver_statuses("TelemetryCollectorUnavailable"):
            raise AcceptanceFailure(
                "A duplicate Collector-specific warning alert was still delivered"
            )
        evidence["collector_target_warning_firing"] = {
            "alertname": "TelemetryTargetDown",
            "severity": "warning",
            "receiver_first_observed_unix_ns": collector_warning_firing_ns,
            "duplicate_collector_specific_rule_absent": True,
        }
        collector_business, _collector_trace = business_probe.run_case("collector_stopped")
        business_matrix["collector_stopped"] = collector_business
        old_source_event = append_stale_gateway_event(runtime)
        failure_context["stage"] = "collector_recovery_after_pipeline_gap"
        collector_recovery_started_ns = time.time_ns()
        acceptance_success(
            runtime.compose("start", "collector", timeout=30),
            "The UUID-owned Collector did not restart after the API readiness probe",
        )
        collector_recovered_ns = time.time_ns()
        recovered_targets = runtime.wait_targets()
        collector_after = runtime.inspect_service("collector")
        if collector_after.get("Id") != collector_id_before:
            raise AcceptanceFailure("Collector recovery replaced its UUID-owned container")
        runtime.wait_app_gateway()

        stale_event_result: dict[str, object] = {}

        def old_source_event_queryable() -> bool:
            nonlocal stale_event_result
            try:
                stale_event_result = verify_stale_gateway_event(runtime, old_source_event)
                return True
            except AcceptanceFailure:
                return False

        runtime.stack.wait_for(
            old_source_event_queryable,
            seconds=45,
            failure="The replayed old-source gateway record was not queryable at producer time",
        )
        evidence["old_source_event_time_window"] = stale_event_result
        recovered_healthchecks: dict[str, object] = {}

        def genuine_gateway_healthchecks_after_collector_recovery() -> bool:
            nonlocal recovered_healthchecks
            recovered_healthchecks = runtime.query_loki_healthchecks(
                start_ns=collector_recovered_ns
            )
            observed_events = recovered_healthchecks.get("observed_events")
            return type(observed_events) is int and observed_events >= 2

        runtime.stack.wait_for(
            genuine_gateway_healthchecks_after_collector_recovery,
            seconds=45,
            failure=(
                "Genuine post-recovery /health/gateway events did not return to Loki "
                "within two gateway healthcheck intervals plus evaluation margin"
            ),
        )
        latest_recovered_event = recovered_healthchecks.get("latest_event_unix_ns")
        if (
            recovered_healthchecks.get("query_start_unix_ns") != collector_recovered_ns
            or type(latest_recovered_event) is not int
            or latest_recovered_event < collector_recovered_ns
        ):
            raise AcceptanceFailure(
                "The returned gateway healthchecks were not fresh after Collector restart"
            )
        gateway_sli_samples_after_collector_recovery = query_gateway_sli_samples(
            gateway_sli_query_expressions
        )
        if any(
            gateway_sli_samples_after_collector_recovery[name] is None
            for name in gateway_sli_ratio_names
        ):
            raise AcceptanceFailure(
                "Gateway ratio samples did not return after genuine healthcheck recovery"
            )
        evidence["gateway_sli_samples_after_collector_recovery"] = {
            "sample_values": gateway_sli_samples_after_collector_recovery,
            "ratio_samples_returned": True,
            "genuine_healthcheck_source": "gateway /health/gateway",
        }
        collector_outage_elapsed_seconds = (
            collector_recovered_ns - collector_outage_started_ns
        ) / 1_000_000_000
        if collector_outage_elapsed_seconds < 90:
            raise AcceptanceFailure(
                "The Collector outage did not span the 60-second absence and 30-second hold"
            )
        pipeline_predicate_after_recovery = runtime.loki_scalar(pipeline_missing_expression)
        if pipeline_predicate_after_recovery is not None:
            raise AcceptanceFailure(
                "Genuine gateway healthchecks did not clear the absence predicate"
            )

        failure_context["stage"] = "collector_pipeline_missing_resolved"
        pipeline_resolved_observed_ns: int | None = None

        def pipeline_missing_resolved_received() -> bool:
            nonlocal pipeline_resolved_observed_ns
            received = any(
                item["status"] == "resolved" and item["severity"] == "critical"
                for item in runtime.alert_receiver_statuses("GatewayPipelineHeartbeatMissing")
            )
            if received and pipeline_resolved_observed_ns is None:
                pipeline_resolved_observed_ns = time.time_ns()
            return received

        runtime.stack.wait_for(
            pipeline_missing_resolved_received,
            seconds=45,
            failure="The local receiver did not receive the resolved gateway pipeline alert",
        )
        evidence["genuine_gateway_healthchecks_after_collector_recovery"] = recovered_healthchecks
        evidence["collector_pipeline_missing_resolved"] = {
            "alertname": "GatewayPipelineHeartbeatMissing",
            "receiver_statuses": runtime.alert_receiver_statuses("GatewayPipelineHeartbeatMissing"),
            "receiver_first_observed_unix_ns": pipeline_resolved_observed_ns,
            "exact_predicate_value": pipeline_predicate_after_recovery,
        }
        collector_warning_resolved_ns: int | None = None

        def collector_warning_resolved_received() -> bool:
            nonlocal collector_warning_resolved_ns
            received = any(
                item["status"] == "resolved" and item["severity"] == "warning"
                for item in runtime.alert_receiver_statuses("TelemetryTargetDown")
            )
            if received and collector_warning_resolved_ns is None:
                collector_warning_resolved_ns = time.time_ns()
            return received

        runtime.stack.wait_for(
            collector_warning_resolved_received,
            seconds=60,
            failure="The consolidated Collector target-down warning did not resolve after recovery",
        )
        evidence["collector_target_warning_resolved"] = {
            "alertname": "TelemetryTargetDown",
            "severity": "warning",
            "receiver_first_observed_unix_ns": collector_warning_resolved_ns,
            "legacy_duplicate_rule_statuses": runtime.alert_receiver_statuses(
                "TelemetryCollectorUnavailable"
            ),
        }
        collector_loss_after = api_telemetry_loss_totals(runtime)
        before_totals = cast(dict[str, float], collector_loss_before["totals"])
        after_totals = cast(dict[str, float], collector_loss_after["totals"])
        collector_loss_delta = {
            name: after_totals[name] - before_totals[name] for name in ("failed", "dropped")
        }
        if any(value < 0 for value in collector_loss_delta.values()) or not any(
            value > 0 for value in collector_loss_delta.values()
        ):
            raise AcceptanceFailure(
                "The API cumulative telemetry loss counters did not expose Collector outage loss"
            )
        evidence["collector_outage_cumulative_loss"] = {
            "before": collector_loss_before,
            "after_recovery": collector_loss_after,
            "delta": collector_loss_delta,
            "counter_values_are_cumulative": True,
        }
        evidence["collector_outage_isolation"] = {
            "collector_stopped_status": "exited",
            "collector_container_preserved": True,
            "api_readiness": readiness,
            "gateway_ready_while_collector_stopped": True,
            "collector_outage_started_unix_ns": collector_outage_started_ns,
            "collector_recovery_started_unix_ns": collector_recovery_started_ns,
            "collector_recovered_unix_ns": collector_recovered_ns,
            "collector_outage_elapsed_seconds": round(collector_outage_elapsed_seconds, 2),
            "collector_recovered": True,
            "targets_after_recovery": recovered_targets,
        }

        failure_context["stage"] = "worker_signal_contract"
        selected_names: dict[str, str] = {}
        baseline: dict[str, dict[str, object]] = {}

        def worker_signals_ready() -> bool:
            nonlocal selected_names, baseline
            selected_names, baseline = runtime.worker_metric_state()
            return {"started", "heartbeat", "last_successful_scan", "last_progress"}.issubset(
                baseline
            )

        failure_context["stage"] = "prometheus_receiver_schema"
        runtime.stack.wait_for(
            worker_signals_ready,
            seconds=60,
            failure="The actual worker exposition omitted required signals",
        )
        evidence["worker_metric_names"] = selected_names
        evidence["worker_series_schema"] = runtime.worker_series_schema()
        evidence["target_info_label_schema"] = runtime.target_info_schema
        evidence["worker_dashboard_metric_inventory"] = runtime.worker_dashboard_metric_inventory()
        receiver_series = (
            runtime.api_json(
                "http://prometheus:9090/api/v1/query?"
                + urlencode({"query": "prl_expected_worker_slot"})
            )
            .get("data", {})
            .get("result", [])
        )
        receiver_slot = next(
            (
                item.get("metric", {})
                for item in receiver_series
                if item.get("metric", {}).get("slot") == "1"
                and item.get("metric", {}).get("job") == "local-alert-receiver"
            ),
            None,
        )
        if receiver_slot is None or receiver_slot.get("instance") != "local_alert_receiver:8088":
            raise AcceptanceFailure("The receiver slot series lost its actual scrape identity")
        evidence["receiver_slot_labels"] = receiver_slot

        failure_context["stage"] = "ops_tls_auth_cidr_and_receiver"
        volumes_before = runtime.volume_snapshot()
        expected_volumes = {
            "mysql_data",
            "gateway_logs",
            "collector_file_storage",
            "prometheus_data",
            "loki_data",
            "tempo_data",
            "grafana_data",
            "alertmanager_data",
        }
        suffixes = {name.removeprefix(runtime.stack.project + "_") for name in volumes_before}
        if suffixes != expected_volumes:
            raise AcceptanceFailure(
                "The UUID-owned stack did not create the exact persistent volumes"
            )
        gateway_volume = next(
            volume for name, volume in volumes_before.items() if name.endswith("_gateway_logs")
        )
        options = _object_mapping(gateway_volume.get("options"))
        tmpfs_options = options.get("o", "")
        if (
            gateway_volume["driver"] != "local"
            or options.get("type") != "tmpfs"
            or not isinstance(tmpfs_options, str)
            or "size=64m" not in tmpfs_options
        ):
            raise AcceptanceFailure("The gateway log spool is not bounded by its 64 MiB tmpfs")
        collector = runtime.inspect_service("collector")
        collector_config = _object_mapping(collector.get("Config"))
        raw_collector_mounts = collector.get("Mounts", [])
        collector_mounts = (
            [_object_mapping(item) for item in raw_collector_mounts]
            if isinstance(raw_collector_mounts, list)
            else []
        )
        collector_mount = next(
            (item for item in collector_mounts if item.get("Destination") == "/var/log/prl"),
            None,
        )
        if (
            collector_mount is None
            or collector_mount.get("RW") is not False
            or collector_config.get("User") != "10001:10001"
            or _object_mapping(collector.get("HostConfig")).get("GroupAdd") != ["101"]
        ):
            raise AcceptanceFailure(
                "The Collector is not using its read-only group-101 spool mount"
            )
        gateway_id = acceptance_success(
            runtime.compose("ps", "-q", "gateway"),
            "The UUID-owned gateway container is missing",
        )
        spool = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "exec",
                    gateway_id,
                    "sh",
                    "-c",
                    "stat -c '%a:%u:%g' /var/log/prl/access.jsonl",
                ]
            ),
            "The JSONL spool file could not be inspected",
        ).strip()
        if spool != "640:101:101":
            raise AcceptanceFailure("The actual gateway spool file permissions/group changed")
        evidence["gateway_spool"] = {
            "mode_uid_gid": spool,
            "collector_user": collector_config.get("User"),
            "collector_group_add": _object_mapping(collector.get("HostConfig")).get("GroupAdd"),
            "read_only": True,
        }

        positive = runtime.run_probe("task6-ops-probe", "task6_ops_probe")
        stage_observations = positive.get("stage_observations")
        if not isinstance(stage_observations, dict):
            raise AcceptanceFailure("The OPS probe omitted safe per-request response observations")
        if (
            positive.get("health") != 200
            or positive.get("unauth_org") != 401
            or positive.get("admin_org") != 200
            or positive.get("datasource_status") != 200
            or positive.get("dashboard_status") != 200
            or positive.get("datasource_uids") != ["prl-loki", "prl-prometheus", "prl-tempo"]
            or positive.get("dashboard_uids") != sorted(OPS_PROBE_DASHBOARD_UIDS)
            or positive.get("trace_link_config_ok") is not True
            or positive.get("tempo_trace_filter_config_ok") is not True
            or positive.get("datasource_unknown_uid_count") != 0
            or positive.get("datasource_malformed_item_count") != 0
            or set(stage_observations) != set(OPS_PROBE_STAGES)
            or stage_observations.get("datasources", {}).get("json_kind") != "list"
            or stage_observations.get("dashboards", {}).get("json_kind") != "list"
        ):
            raise AcceptanceFailure(
                "The verified OPS HTTPS/auth/datasource probe failed: "
                + json.dumps(runtime.last_operations_probe, sort_keys=True, separators=(",", ":"))
            )
        if not all(
            isinstance(positive.get(name), int)
            for name in ("ops_business_status", "ops_health_status")
        ):
            raise AcceptanceFailure("The OPS-vhost boundary probes did not complete")
        operations_records = runtime.gateway_operations_records()
        required_statuses = [
            positive["ops_business_status"],
            positive["ops_health_status"],
        ]
        if any(
            sum(record.get("status") == status for record in operations_records)
            < required_statuses.count(status)
            for status in set(required_statuses)
        ):
            raise AcceptanceFailure("OPS business/health paths were not logged as operations")
        evidence["operations_vhost"] = {
            "business_shaped_path_status": positive["ops_business_status"],
            "health_shaped_path_status": positive["ops_health_status"],
            "event": "gateway.operations",
            "classification": "out_of_scope",
            "route": "__unmatched__",
            "log_fields": sorted(operations_records[-1]),
        }
        grafana = runtime.inspect_service("grafana")
        grafana_config = _object_mapping(grafana.get("Config"))
        raw_grafana_env = grafana_config.get("Env")
        grafana_env = set(raw_grafana_env) if isinstance(raw_grafana_env, list) else set()
        if (
            "GF_AUTH_ANONYMOUS_ENABLED=false" not in grafana_env
            or "GF_USERS_ALLOW_SIGN_UP=false" not in grafana_env
            or "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana-admin-password"
            not in grafana_env
        ):
            raise AcceptanceFailure("Grafana anonymous/signup/admin-file auth settings changed")
        evidence["grafana"] = {
            "anonymous_org": positive["unauth_org"],
            "authenticated_admin_org": positive["admin_org"],
            "datasources": positive["datasource_uids"],
            "dashboards": positive["dashboard_uids"],
            "loki_trace_metadata_derived_field": positive["trace_link_config_ok"],
            "tempo_to_loki_trace_filter": positive["tempo_trace_filter_config_ok"],
            "anonymous_auth_disabled": True,
            "signup_disabled": True,
        }
        screenshot_evidence = capture_dashboard_screenshots(runtime, evidence_root=screenshot_root)
        evidence["dashboard_screenshots"] = screenshot_evidence

        negative = runtime.run_probe("task6-ops-denied-probe", "task6_ops_denied_probe")
        if negative.get("spoofed_forwarded_status") != 403:
            raise AcceptanceFailure("The OPS CIDR guard trusted a spoofed forwarded address")
        evidence["ops_cidr"] = {
            "allowed_loopback_admin": True,
            "allowed_published_host_peer": str(OPS_EDGE_TEST_GATEWAY),
            "published_host_binding": "127.0.0.1",
            "spoofed_forwarded_peer_status": 403,
        }

        failure_context["stage"] = "receiver_self_health"
        health = runtime.wait_receiver_health()
        failure_context["receiver_health"] = health
        if not receiver_health_is_passed(health):
            raise AcceptanceFailure("The receiver did not observe the telemetry backends healthy")
        evidence["receiver_self_health"] = health

        failure_context["stage"] = "gateway_rule_notification_firing"
        rules_document = yaml.safe_load(
            (ROOT / "tools/infra/loki-rules/fake/gateway-sli.yml").read_text(encoding="utf-8")
        )
        recordings = {
            rule["record"]: rule["expr"]
            for group in rules_document["groups"]
            for rule in group["rules"]
            if "record" in rule
        }
        alert_expression = next(
            rule["expr"]
            for group in rules_document["groups"]
            for rule in group["rules"]
            if rule.get("alert") == "GatewayFastSuccessBelowSLO"
        )
        success_expression = recordings["prl:gateway_successful_requests:sum5m"]
        fast_count_expression = recordings["prl:gateway_fast_successful_requests:sum5m"]
        ratio_expression = recordings["prl:gateway_fast_success:ratio5m"]

        firing_receiver_baseline = runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
        fresh_slow_event_count = 100
        firing_fixture_push_started_at = (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        runtime.push_gateway_json_logs(
            [
                {
                    "event": "gateway.request",
                    "classification": "success",
                    "route": "/__task8_fresh_all_slow",
                    "duration_seconds": 0.75,
                }
                for _ in range(fresh_slow_event_count)
            ]
        )
        evidence["gateway_rule_firing_fixture"] = {
            "push_started_at_utc": firing_fixture_push_started_at,
            "successful_events": fresh_slow_event_count,
            "duration_seconds": 0.75,
        }

        firing_snapshot: dict[str, float | None] = {}
        firing_snapshot_refreshed_at = 0.0
        fresh_firing_delivery: dict[str, str] | None = None

        def gateway_rule_firing_received() -> bool:
            nonlocal firing_snapshot, firing_snapshot_refreshed_at, fresh_firing_delivery
            now = time.monotonic()
            if now - firing_snapshot_refreshed_at >= 5 or not firing_snapshot:
                firing_snapshot = {
                    "successful_requests": runtime.loki_scalar(success_expression),
                    "fast_successful_requests": runtime.loki_scalar(fast_count_expression),
                    "fast_success_ratio": runtime.loki_scalar(ratio_expression),
                    "exact_alert_predicate": runtime.loki_scalar(alert_expression),
                }
                firing_snapshot_refreshed_at = now
                evidence["gateway_rule_pre_recovery_query"] = firing_snapshot
            if not gateway_sli_firing_truth_table_is_active(
                firing_snapshot["successful_requests"],
                firing_snapshot["fast_successful_requests"],
                firing_snapshot["fast_success_ratio"],
                firing_snapshot["exact_alert_predicate"],
            ):
                return False
            current_statuses = runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
            new_firing_records = new_alert_statuses_since_snapshot(
                firing_receiver_baseline,
                current_statuses,
                status="firing",
                severity="warning",
            )
            if not new_firing_records:
                return False
            fresh_firing_delivery = new_firing_records[0]
            evidence["gateway_rule_firing_delivery"] = fresh_firing_delivery
            return True

        runtime.stack.wait_for(
            gateway_rule_firing_received,
            seconds=180,
            failure="The pinned Loki ruler did not deliver a fresh gateway SLI firing alert",
        )
        evidence["gateway_rule_firing"] = {
            "alertname": "GatewayFastSuccessBelowSLO",
            "status": "firing",
            "receiver_delivery": fresh_firing_delivery,
            "fresh_successful_events": fresh_slow_event_count,
            "fixture_push_started_at_utc": firing_fixture_push_started_at,
        }

        success_count = firing_snapshot["successful_requests"]
        fast_count = firing_snapshot["fast_successful_requests"]
        current_ratio = firing_snapshot["fast_success_ratio"]
        current_predicate = firing_snapshot["exact_alert_predicate"]
        if (
            success_count is None
            or fast_count is None
            or current_ratio is None
            or current_predicate is None
        ):
            raise AcceptanceFailure("The live gateway SLI firing snapshot is incomplete")
        healthy_needed = math.floor(max(0.0, 0.95 * success_count - fast_count) / 0.05) + 101
        if not 1 <= healthy_needed <= 5000:
            raise AcceptanceFailure("The calculated bounded recovery fixture exceeded its cap")
        recovery_events = [
            {
                "event": "gateway.request",
                "classification": "success",
                "route": "/__task7_recovery",
                "duration_seconds": 0.2,
            }
            for _ in range(healthy_needed)
        ]
        recovery_receiver_baseline = runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
        recovery_fixture_push_started_at = (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        runtime.push_gateway_json_logs(recovery_events)
        evidence["gateway_rule_recovery_fixture"] = {
            "push_started_at_utc": recovery_fixture_push_started_at,
            "successful_events": healthy_needed,
            "duration_seconds": 0.2,
        }
        recovered_success_count = runtime.loki_scalar(success_expression)
        recovered_fast_count = runtime.loki_scalar(fast_count_expression)
        recovered_ratio = runtime.loki_scalar(ratio_expression)
        recovered_predicate = runtime.loki_scalar(alert_expression)
        evidence["recovery_query_validation"] = {
            "successful_requests": recovered_success_count,
            "fast_successful_requests": recovered_fast_count,
            "fast_success_ratio": recovered_ratio,
            "exact_alert_predicate": recovered_predicate,
            "healthy_success_events_pushed": healthy_needed,
        }
        if (
            recovered_success_count is None
            or recovered_fast_count is None
            or recovered_ratio is None
            or recovered_ratio <= 0.95
            or recovered_predicate is not None
        ):
            raise AcceptanceFailure("The recovery fixture did not clear the live gateway SLI")
        failure_context["stage"] = "gateway_rule_notification_resolved"

        fresh_resolved_delivery: dict[str, str] | None = None

        def gateway_rule_resolved_received() -> bool:
            nonlocal fresh_resolved_delivery
            current_statuses = runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
            new_resolved_records = new_alert_statuses_since_snapshot(
                recovery_receiver_baseline,
                current_statuses,
                status="resolved",
                severity="warning",
            )
            if not new_resolved_records:
                return False
            fresh_resolved_delivery = new_resolved_records[0]
            evidence["gateway_rule_resolved_delivery"] = fresh_resolved_delivery
            return True

        runtime.stack.wait_for(
            gateway_rule_resolved_received,
            seconds=90,
            failure="The local receiver did not receive the resolved gateway SLI alert",
        )
        evidence["gateway_rule_resolved"] = {
            "alertname": "GatewayFastSuccessBelowSLO",
            "status": "resolved",
            "healthy_success_events": len(recovery_events),
            "observed_recovery_ratio": recovered_ratio,
            "fixture_push_started_at_utc": recovery_fixture_push_started_at,
            "receiver_delivery": fresh_resolved_delivery,
        }
        evidence["gateway_sli_truth_table"] = runtime.gateway_sli_truth_table()
        evidence["alert_summaries"] = runtime.synthetic_alert_summaries()

        health_logs = []
        health_deadline = time.monotonic() + 15
        while time.monotonic() < health_deadline:
            state = acceptance_json(
                acceptance_invoke(["docker", "inspect", gateway_id]),
                "The gateway Docker healthcheck could not be inspected",
            )[0].get("State", {})
            health_logs = (state.get("Health") or {}).get("Log", [])
            if len(health_logs) >= 2:
                break
            time.sleep(0.5)
        if len(health_logs) < 2 or any(item.get("ExitCode") != 0 for item in health_logs[-2:]):
            raise AcceptanceFailure("The gateway did not produce recurring healthy self-checks")
        first = datetime.fromisoformat(health_logs[-2]["Start"].replace("Z", "+00:00"))
        second = datetime.fromisoformat(health_logs[-1]["Start"].replace("Z", "+00:00"))
        interval = (second - first).total_seconds()
        if not 1 <= interval <= 15:
            raise AcceptanceFailure("The genuine gateway healthcheck cadence changed")
        evidence["gateway_healthcheck"] = {
            "successful_runs": 2,
            "docker_interval_seconds": round(interval, 2),
            "loki": runtime.query_loki_healthchecks(),
        }

        failure_context["stage"] = "business_backend_outage_matrix"
        for backend in ("prometheus", "loki", "tempo"):
            business_matrix[f"{backend}_stopped"] = run_backend_outage(business_probe, backend)
        evidence["business_outage_matrix"] = business_matrix
        failure_context["stage"] = "grafana_outage_isolation"
        acceptance_success(
            runtime.compose("stop", "--timeout", "10", "grafana", timeout=30),
            "The UUID-owned Grafana could not stop for outage isolation",
        )
        outage = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if outage[0] != 200:
            raise AcceptanceFailure("Application HTTPS readiness depended on Grafana availability")
        acceptance_success(
            runtime.compose("up", "-d", "--wait", "--wait-timeout", "90", "grafana", timeout=120),
            "Grafana did not recover after its isolated outage",
        )
        runtime.wait_targets()
        evidence["grafana_outage_isolation"] = "application HTTPS remained ready"

        old_volumes = runtime.volume_snapshot()
        failure_context["stage"] = "worker_pre_restart_snapshot"
        previous_fields = (
            "previous_started_timestamp_seconds",
            "previous_last_successful_scan_timestamp_seconds",
            "previous_last_progress_timestamp_seconds",
        )
        worker_restart_context: dict[str, object] = {}
        failure_context["worker_restart_observations"] = worker_restart_context
        old_owner_progress = runtime.stack.wait_heartbeat()

        def validated_epoch_seconds(value: object) -> float | None:
            if type(value) not in (int, float):
                return None
            timestamp = float(value)
            if not math.isfinite(timestamp) or not 0 < timestamp < 10_000_000_000:
                return None
            return round(timestamp, 3)

        def timestamp_delta_seconds(value: object, baseline: object) -> float | None:
            timestamp = validated_epoch_seconds(value)
            previous = validated_epoch_seconds(baseline)
            if timestamp is None or previous is None:
                return None
            delta = timestamp - previous
            if not math.isfinite(delta) or abs(delta) >= 10_000_000_000:
                return None
            return round(delta, 3)

        def capture_worker_generation(
            *, deadline: float | None = None
        ) -> tuple[str | None, dict[str, object]]:
            container_id = None
            started_at = None
            restart_count = None
            service_state: dict[str, object] = {
                "status": None,
                "exit_code": None,
                "restart_count": None,
                "oom_killed": None,
            }
            generation: dict[str, object] = {
                "container_id_valid": False,
                "container_started_at_unix_seconds": None,
                "restart_count": None,
                "container_state": service_state,
            }
            try:
                if deadline is None:
                    record = runtime.inspect_service("worker", timeout=5)
                else:
                    record = runtime.inspect_service("worker", timeout=5, deadline=deadline)
            except Exception as error:
                generation["inspection_error_type"] = type(error).__name__
                return None, generation

            candidate_id = record.get("Id")
            if isinstance(candidate_id, str) and re.fullmatch(r"[0-9a-f]{64}", candidate_id):
                container_id = candidate_id
            state = _object_mapping(record.get("State"))
            raw_started_at = state.get("StartedAt")
            if isinstance(raw_started_at, str):
                try:
                    parsed_started_at = datetime.fromisoformat(
                        raw_started_at.replace("Z", "+00:00")
                    )
                    if (
                        parsed_started_at.tzinfo is not None
                        and parsed_started_at.utcoffset() is not None
                    ):
                        started_at = validated_epoch_seconds(parsed_started_at.timestamp())
                except (OSError, OverflowError, ValueError):
                    pass
            raw_restart_count = record.get("RestartCount")
            if type(raw_restart_count) is int and 0 <= raw_restart_count <= 1_000_000_000:
                restart_count = raw_restart_count
            raw_status = state.get("Status")
            service_state = {
                "status": (
                    raw_status
                    if isinstance(raw_status, str)
                    and raw_status
                    in {"created", "restarting", "running", "removing", "paused", "exited", "dead"}
                    else None
                ),
                "exit_code": state.get("ExitCode") if type(state.get("ExitCode")) is int else None,
                "oom_killed": (
                    state.get("OOMKilled") if type(state.get("OOMKilled")) is bool else None
                ),
            }
            generation.update(
                {
                    "container_id_valid": container_id is not None,
                    "container_started_at_unix_seconds": started_at,
                    "restart_count": restart_count,
                    "container_state": service_state,
                }
            )

            return container_id, generation

        def capture_lifecycle_event_summary(container_id: str | None) -> dict[str, object]:
            events = ("starting", "stopping", "stopped", "fatal")
            counts = dict.fromkeys(events, 0)
            valid_event_worker_counts = dict.fromkeys(events, 0)
            latest_event_worker_matches: dict[str, bool | None] = {event: None for event in events}
            event_logs_available = False
            event_log_error_type = None
            event_log_error_category = None
            if container_id is not None:
                try:
                    log_result = acceptance_invoke(
                        ["docker", "logs", "--tail", "200", container_id], timeout=3
                    )
                except Exception as error:
                    event_log_error_type = type(error).__name__
                else:
                    if log_result.returncode == 0:
                        event_logs_available = True
                        for line in (log_result.stdout or "").splitlines():
                            try:
                                item = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(item, dict):
                                continue
                            event = item.get("event")
                            if not isinstance(event, str) or event not in counts:
                                continue
                            counts[event] = min(counts[event] + 1, 200)
                            worker_uuid = item.get("worker_id")
                            if isinstance(worker_uuid, str) and re.fullmatch(
                                r"[0-9a-f]{32}", worker_uuid
                            ):
                                valid_event_worker_counts[event] += 1
                                latest_event_worker_matches[event] = (
                                    worker_uuid == old_owner_progress.worker_id
                                )
                            else:
                                latest_event_worker_matches[event] = None
                    else:
                        event_log_error_category = "nonzero_exit"
            return {
                "event_logs_available": event_logs_available,
                "lifecycle_events": {
                    event: {
                        "count": counts[event],
                        "worker_id_count": valid_event_worker_counts[event],
                        "latest_worker_matches_pre_fixture": latest_event_worker_matches[event],
                    }
                    for event in events
                },
                **(
                    {"event_log_error_type": event_log_error_type}
                    if event_log_error_type is not None
                    else {}
                ),
                **(
                    {"event_log_error_category": event_log_error_category}
                    if event_log_error_category is not None
                    else {}
                ),
            }

        pre_fixture_container_id, pre_fixture_generation = capture_worker_generation()
        pinned_started_at = validated_epoch_seconds(
            pre_fixture_generation.get("container_started_at_unix_seconds")
        )
        pinned_status = _object_mapping(pre_fixture_generation.get("container_state")).get("status")
        freshness_diagnostic: dict[str, object] = {
            "poll_count": 0,
            "last_poll_duration_seconds": None,
            "pinned_container_started_at_unix_seconds": pinned_started_at,
            "pinned_container_status": pinned_status,
            "last_metric_started_value_seconds": None,
            "last_metric_sample_timestamp_seconds": None,
            "last_metric_started_delta_seconds": None,
            "last_query_stage": None,
            "last_api_status": None,
            "last_poll_status": None,
            "verified_container_id_matches_pin": None,
            "verified_container_started_at_matches_pin": None,
            "verified_container_started_at_unix_seconds": None,
            "verified_container_status": None,
            "failure_category": None,
        }

        def fail_started_metric_freshness(message: str, category: str) -> None:
            freshness_diagnostic["failure_category"] = category
            worker_restart_context["started_metric_freshness"] = dict(freshness_diagnostic)
            raise AcceptanceFailure(message)

        if (
            pre_fixture_container_id is None
            or pinned_started_at is None
            or pinned_status != "running"
        ):
            fail_started_metric_freshness(
                "The running worker container generation could not be pinned",
                "container_pin_unavailable",
            )

        freshness_deadline = time.monotonic() + 45
        pre_restart_state: dict[str, dict[str, object]] | None = None
        while time.monotonic() < freshness_deadline:
            poll_started = time.monotonic()
            freshness_diagnostic["poll_count"] = int(freshness_diagnostic["poll_count"]) + 1
            try:
                _candidate_names, candidate_state = runtime.worker_metric_state(
                    deadline=freshness_deadline
                )
            except (AcceptanceFailure, httpx.HTTPError, OSError, ValueError):
                query_diagnostic = runtime.last_worker_metric_query_diagnostic
                freshness_diagnostic["last_metric_started_value_seconds"] = None
                freshness_diagnostic["last_metric_sample_timestamp_seconds"] = None
                freshness_diagnostic["last_metric_started_delta_seconds"] = None
                freshness_diagnostic["last_query_stage"] = (
                    query_diagnostic.get("query_stage")
                    if isinstance(query_diagnostic, dict)
                    and isinstance(query_diagnostic.get("query_stage"), str)
                    and query_diagnostic.get("query_stage")
                    in {"metric_names", "metric_series", "complete"}
                    else None
                )
                freshness_diagnostic["last_api_status"] = (
                    query_diagnostic.get("api_status")
                    if isinstance(query_diagnostic, dict)
                    and isinstance(query_diagnostic.get("api_status"), str)
                    and query_diagnostic.get("api_status") in {"success", "error", "invalid"}
                    else None
                )
                freshness_diagnostic["last_poll_status"] = "query_unavailable"
                freshness_diagnostic["last_poll_duration_seconds"] = round(
                    time.monotonic() - poll_started, 3
                )
            else:
                freshness_diagnostic["last_query_stage"] = "complete"
                freshness_diagnostic["last_api_status"] = "success"
                metric_started_value = _worker_metric_value(candidate_state, "started")
                started_sample = _object_mapping(candidate_state.get("started")).get(
                    "sample_timestamp"
                )
                freshness_diagnostic["last_metric_started_value_seconds"] = validated_epoch_seconds(
                    metric_started_value
                )
                freshness_diagnostic["last_metric_sample_timestamp_seconds"] = (
                    validated_epoch_seconds(started_sample)
                )
                freshness_diagnostic["last_metric_started_delta_seconds"] = timestamp_delta_seconds(
                    metric_started_value, pinned_started_at
                )
                freshness_diagnostic["last_poll_duration_seconds"] = round(
                    time.monotonic() - poll_started, 3
                )
                if time.monotonic() >= freshness_deadline:
                    freshness_diagnostic["last_poll_status"] = "deadline_expired_after_query"
                    break
                if runtime.worker_started_value_matches_container_generation(
                    metric_started_value, pinned_started_at
                ):
                    if time.monotonic() >= freshness_deadline:
                        freshness_diagnostic["last_poll_status"] = (
                            "deadline_expired_before_verification"
                        )
                        fail_started_metric_freshness(
                            "The worker freshness deadline expired before generation verification",
                            "freshness_deadline",
                        )
                    verified_container_id, verified_generation = capture_worker_generation(
                        deadline=freshness_deadline
                    )
                    verified_started_at = validated_epoch_seconds(
                        verified_generation.get("container_started_at_unix_seconds")
                    )
                    verified_status = _object_mapping(
                        verified_generation.get("container_state")
                    ).get("status")
                    id_matches_pin = (
                        verified_container_id == pre_fixture_container_id
                        if verified_container_id is not None
                        else False
                    )
                    started_at_matches_pin = (
                        verified_started_at == pinned_started_at
                        if verified_started_at is not None
                        else False
                    )
                    freshness_diagnostic.update(
                        {
                            "verified_container_id_matches_pin": id_matches_pin,
                            "verified_container_started_at_matches_pin": started_at_matches_pin,
                            "verified_container_started_at_unix_seconds": verified_started_at,
                            "verified_container_status": verified_status,
                            "last_poll_duration_seconds": round(time.monotonic() - poll_started, 3),
                        }
                    )
                    if time.monotonic() >= freshness_deadline:
                        freshness_diagnostic["last_poll_status"] = (
                            "deadline_expired_during_verification"
                        )
                        fail_started_metric_freshness(
                            "The worker freshness deadline expired during generation verification",
                            "freshness_deadline",
                        )
                    if (
                        not id_matches_pin
                        or not started_at_matches_pin
                        or verified_status != "running"
                    ):
                        freshness_diagnostic["last_poll_status"] = "container_generation_changed"
                        fail_started_metric_freshness(
                            "The worker container generation changed before its metric baseline "
                            "was captured",
                            "container_generation_changed",
                        )
                    pre_fixture_container_id = verified_container_id
                    pre_fixture_generation = verified_generation
                    pre_restart_state = candidate_state
                    freshness_diagnostic["last_poll_status"] = "matched_generation"
                    break
                freshness_diagnostic["last_poll_status"] = "stale_metric_value"

            time.sleep(min(0.5, max(0.0, freshness_deadline - time.monotonic())))

        if pre_restart_state is None:
            fail_started_metric_freshness(
                "The worker started metric did not match the inspected container generation",
                "freshness_deadline",
            )
        worker_restart_context["started_metric_freshness"] = dict(freshness_diagnostic)
        pre_restart_summary = runtime.worker_restart_observation_summary(
            pre_restart_state, pre_restart_state
        )
        worker_restart_context["pre_restart"] = {
            name: pre_restart_summary.get(name) for name in previous_fields
        }
        if any(pre_restart_summary.get(name) is None for name in previous_fields):
            raise AcceptanceFailure(
                "The current worker omitted finite same-slot baseline timestamps"
            )
        old_started = _worker_metric_value(pre_restart_state, "started")
        if old_started is None:
            raise AcceptanceFailure("The current worker omitted its numeric start timestamp")

        worker_alert_firing_baselines = {
            name: worker_receiver_snapshot(name)
            for name in (
                "WorkerHeartbeatStale",
                "WorkerProgressStale",
                "WorkerBacklogSnapshotStale",
            )
        }
        recovery_fixture = business_probe.prepare_due_expiry_fixture()
        fixture_container_id, fixture_generation = capture_worker_generation()

        def compare_worker_generations(
            current_id: str | None,
            current: dict[str, object],
            previous_id: str | None,
            previous: dict[str, object],
        ) -> dict[str, object]:
            current_restart_count = current.get("restart_count")
            previous_restart_count = previous.get("restart_count")
            return {
                "container_identity_matches": (
                    current_id == previous_id
                    if current_id is not None and previous_id is not None
                    else None
                ),
                "started_at_delta_seconds": timestamp_delta_seconds(
                    current.get("container_started_at_unix_seconds"),
                    previous.get("container_started_at_unix_seconds"),
                ),
                "restart_count_delta": (
                    current_restart_count - previous_restart_count
                    if type(current_restart_count) is int and type(previous_restart_count) is int
                    else None
                ),
            }

        def stopped_metric_generation_correlation(
            stopped_observation: dict[str, object],
            timeout_generation: dict[str, object],
        ) -> dict[str, object]:
            timestamp_values = _object_mapping(stopped_observation.get("timestamp_values"))
            sample_timestamps = _object_mapping(stopped_observation.get("sample_timestamps"))
            final_metric_started = validated_epoch_seconds(
                timestamp_values.get("started_timestamp_seconds")
            )
            final_sample_started = validated_epoch_seconds(sample_timestamps.get("started"))
            pre_fixture_metric_started = validated_epoch_seconds(old_started)
            pre_fixture_sample_started = validated_epoch_seconds(
                _object_mapping(pre_restart_state.get("started")).get("sample_timestamp")
            )
            return {
                "pre_fixture_metric_started_timestamp_seconds": pre_fixture_metric_started,
                "pre_fixture_metric_started_sample_timestamp_seconds": pre_fixture_sample_started,
                "pre_fixture_metric_minus_pre_fixture_container_started_seconds": (
                    timestamp_delta_seconds(
                        pre_fixture_metric_started,
                        pre_fixture_generation.get("container_started_at_unix_seconds"),
                    )
                ),
                "final_poll_metric_started_timestamp_seconds": final_metric_started,
                "final_poll_started_sample_timestamp_seconds": final_sample_started,
                "final_poll_metric_started_delta_from_pre_fixture_metric_seconds": (
                    timestamp_delta_seconds(final_metric_started, pre_fixture_metric_started)
                ),
                "final_poll_started_sample_delta_from_pre_fixture_sample_seconds": (
                    timestamp_delta_seconds(final_sample_started, pre_fixture_sample_started)
                ),
                "final_poll_started_sample_minus_metric_started_seconds": (
                    timestamp_delta_seconds(final_sample_started, final_metric_started)
                ),
                "final_poll_metric_minus_container_started_seconds": {
                    "before_fixture": timestamp_delta_seconds(
                        final_metric_started,
                        pre_fixture_generation.get("container_started_at_unix_seconds"),
                    ),
                    "at_fixture": timestamp_delta_seconds(
                        final_metric_started,
                        fixture_generation.get("container_started_at_unix_seconds"),
                    ),
                    "at_timeout": timestamp_delta_seconds(
                        final_metric_started,
                        timeout_generation.get("container_started_at_unix_seconds"),
                    ),
                },
            }

        worker_restart_context["due_expiry_fixture_ready"] = True
        worker_container_id = recovery_fixture.get("worker_container_id")
        mysql_container_id = runtime.inspect_service("mysql").get("Id")
        if (
            not isinstance(worker_container_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", worker_container_id)
            or not isinstance(mysql_container_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", mysql_container_id)
        ):
            raise AcceptanceFailure("The UUID-owned worker or MySQL identity was invalid")

        stopped_baseline: dict[str, dict[str, object]] = {}
        previous_stopped_signature: tuple[object, ...] | None = None
        stopped_signature_stable_since: float | None = None
        stopped_metric_polls = 0

        def capture_stopped_baseline() -> dict[str, dict[str, object]]:
            nonlocal previous_stopped_signature, stopped_baseline, stopped_signature_stable_since

            def record_stopped_metric_poll(
                poll_started: float,
                *,
                candidate: dict[str, dict[str, object]] | None = None,
                summary: dict[str, object] | None = None,
                error: Exception | None = None,
            ) -> None:
                diagnostic = runtime.last_worker_metric_query_diagnostic
                diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
                poll_observation: dict[str, object] = {
                    "poll_count": stopped_metric_polls,
                    "last_poll_duration_seconds": round(time.monotonic() - poll_started, 3),
                    "query_stage": diagnostic.get("query_stage"),
                    "api_status": diagnostic.get("api_status"),
                    "selected_roles": diagnostic.get("selected_roles", []),
                    "candidate_counts": diagnostic.get("candidate_counts", {}),
                    "sample_timestamps": diagnostic.get("sample_timestamps", {}),
                    "available_roles": sorted(candidate) if candidate is not None else [],
                    "started_unchanged": (
                        summary.get("started_unchanged")
                        if isinstance(summary, dict)
                        and type(summary.get("started_unchanged")) is bool
                        else None
                    ),
                    "series_identity_matches": (
                        summary.get("series_identity_matches")
                        if isinstance(summary, dict)
                        and type(summary.get("series_identity_matches")) is bool
                        else None
                    ),
                    "timestamp_values": {
                        name: summary.get(name) if summary else None
                        for name in (
                            "started_timestamp_seconds",
                            "last_successful_scan_timestamp_seconds",
                            "last_progress_timestamp_seconds",
                        )
                    },
                    "signature_unchanged_from_previous_poll": None,
                }
                if error is not None:
                    poll_observation["poll_failure_type"] = type(error).__name__
                    poll_observation["poll_failure_category"] = (
                        "timeout"
                        if isinstance(error, (TimeoutError, httpx.TimeoutException))
                        else "acceptance_failure"
                        if isinstance(error, AcceptanceFailure)
                        else "http_error"
                        if isinstance(error, httpx.HTTPError)
                        else "os_error"
                        if isinstance(error, OSError)
                        else "invalid_value"
                    )
                worker_restart_context["stopped_metric_settle"] = poll_observation

            def stopped_service_state_summary(service: str) -> dict[str, object]:
                try:
                    record = runtime.inspect_service(service, timeout=10)
                except Exception as error:
                    return {"inspection_error_type": type(error).__name__}
                state = _object_mapping(record.get("State"))
                status = state.get("Status")
                return {
                    "status": status if isinstance(status, str) else None,
                    "exit_code": (
                        state.get("ExitCode") if type(state.get("ExitCode")) is int else None
                    ),
                    "restart_count": (
                        record.get("RestartCount")
                        if type(record.get("RestartCount")) is int
                        else None
                    ),
                    "oom_killed": (
                        state.get("OOMKilled") if type(state.get("OOMKilled")) is bool else None
                    ),
                }

            def stable_stopped_metrics() -> bool:
                nonlocal previous_stopped_signature, stopped_baseline
                nonlocal stopped_signature_stable_since
                nonlocal stopped_metric_polls
                stopped_metric_polls += 1
                poll_started = time.monotonic()
                try:
                    _names, candidate = runtime.worker_metric_state()
                    summary = runtime.worker_restart_observation_summary(
                        pre_restart_state, candidate
                    )
                except (AcceptanceFailure, httpx.HTTPError, OSError, ValueError) as error:
                    record_stopped_metric_poll(poll_started, error=error)
                    return False
                record_stopped_metric_poll(poll_started, candidate=candidate, summary=summary)
                if (
                    summary.get("started_unchanged") is not True
                    or summary.get("series_identity_matches") is not True
                ):
                    return False
                roles = ("started", "last_successful_scan", "last_progress")
                signature_items: list[object] = []
                for role in roles:
                    metric = candidate.get(role)
                    if not isinstance(metric, dict):
                        return False
                    labels = metric.get("labels")
                    if not isinstance(labels, dict):
                        return False
                    signature_items.append(
                        (
                            role,
                            metric.get("name"),
                            tuple(sorted(labels.items())),
                            metric.get("value"),
                        )
                    )
                signature = tuple(signature_items)
                now = time.monotonic()
                signature_unchanged = signature == previous_stopped_signature
                stopped_observation = worker_restart_context.get("stopped_metric_settle")
                if isinstance(stopped_observation, dict):
                    stopped_observation["signature_unchanged_from_previous_poll"] = (
                        signature_unchanged
                    )
                if signature_unchanged:
                    if stopped_signature_stable_since is None:
                        stopped_signature_stable_since = now
                    if now - stopped_signature_stable_since >= WORKER_METRIC_SETTLE_SECONDS:
                        stopped_baseline = candidate
                        return True
                else:
                    stopped_signature_stable_since = now
                previous_stopped_signature = signature
                return False

            try:
                runtime.stack.wait_for(
                    stable_stopped_metrics,
                    seconds=45,
                    failure="Worker points did not settle after the old worker stopped",
                )
            except AcceptanceFailure:
                stopped_observation = worker_restart_context.get("stopped_metric_settle")
                if not isinstance(stopped_observation, dict):
                    stopped_observation = {
                        "poll_count": stopped_metric_polls,
                        "last_poll_duration_seconds": None,
                    }
                    worker_restart_context["stopped_metric_settle"] = stopped_observation
                stopped_observation["service_states_at_timeout"] = {
                    "worker": stopped_service_state_summary("worker"),
                    "mysql": stopped_service_state_summary("mysql"),
                }
                timeout_container_id, timeout_generation = capture_worker_generation()
                event_summaries_by_container: dict[str | None, dict[str, object]] = {}
                for container_id, generation in (
                    (pre_fixture_container_id, pre_fixture_generation),
                    (fixture_container_id, fixture_generation),
                    (timeout_container_id, timeout_generation),
                ):
                    if container_id not in event_summaries_by_container:
                        event_summaries_by_container[container_id] = (
                            capture_lifecycle_event_summary(container_id)
                        )
                    generation["lifecycle_event_summary"] = event_summaries_by_container[
                        container_id
                    ]
                stopped_observation["worker_generation"] = {
                    "before_fixture": pre_fixture_generation,
                    "at_fixture": fixture_generation,
                    "at_timeout": timeout_generation,
                    "fixture_vs_before_fixture": compare_worker_generations(
                        fixture_container_id,
                        fixture_generation,
                        pre_fixture_container_id,
                        pre_fixture_generation,
                    ),
                    "timeout_vs_before_fixture": compare_worker_generations(
                        timeout_container_id,
                        timeout_generation,
                        pre_fixture_container_id,
                        pre_fixture_generation,
                    ),
                    "timeout_vs_fixture": compare_worker_generations(
                        timeout_container_id,
                        timeout_generation,
                        fixture_container_id,
                        fixture_generation,
                    ),
                    "metric_started_correlation": stopped_metric_generation_correlation(
                        stopped_observation, timeout_generation
                    ),
                }
                raise
            return stopped_baseline

        heartbeat_stale_episode: dict[str, object] = {}
        worker_alert_resolution_baselines: dict[str, list[dict[str, str]]] = {}

        def wait_for_heartbeat_stale_after_worker_exit() -> None:
            stopped_record = runtime.inspect_service("worker")
            stopped_state = _object_mapping(stopped_record.get("State"))
            stopped_labels = _object_mapping(
                _object_mapping(stopped_record.get("Config")).get("Labels")
            )
            raw_finished_at = stopped_state.get("FinishedAt")
            if (
                stopped_record.get("Id") != worker_container_id
                or stopped_state.get("Status") != "exited"
                or stopped_labels.get("com.docker.compose.project") != runtime.stack.project
                or not isinstance(raw_finished_at, str)
                or raw_finished_at.startswith("0001-")
            ):
                raise AcceptanceFailure(
                    "The stopped worker exit time or UUID ownership was invalid"
                )
            try:
                finished_at = normalize_alertmanager_timestamp(raw_finished_at)
                deadline = MonotonicDeadline.from_utc_timestamp(raw_finished_at, 90)
            except ValueError:
                raise AcceptanceFailure(
                    "The stopped worker exit time was invalid or too old"
                ) from None
            firing = wait_for_worker_alert_firing(
                "WorkerHeartbeatStale",
                worker_alert_firing_baselines["WorkerHeartbeatStale"],
                deadline,
            )
            heartbeat_stale_episode.update(
                {
                    "stimulus": {
                        "worker_container_id_matches": True,
                        "worker_finished_at_utc": finished_at,
                    },
                    "firing": firing,
                }
            )
            worker_alert_resolution_baselines["WorkerHeartbeatStale"] = worker_receiver_snapshot(
                "WorkerHeartbeatStale"
            )
            worker_alert_evidence["WorkerHeartbeatStale"] = heartbeat_stale_episode

        failure_context["stage"] = "worker_and_database_restart"
        stopped_baseline = restart_worker_while_database_is_unavailable(
            runtime.compose,
            runtime.inspect_service,
            worker_container_id=worker_container_id,
            mysql_container_id=mysql_container_id,
            capture_baseline=capture_stopped_baseline,
            on_worker_stopped=wait_for_heartbeat_stale_after_worker_exit,
        )
        worker_restart_context["stopped_baseline"] = {
            name: _worker_metric_value(stopped_baseline, role)
            for name, role in (
                ("started_timestamp_seconds", "started"),
                ("last_successful_scan_timestamp_seconds", "last_successful_scan"),
                ("last_progress_timestamp_seconds", "last_progress"),
            )
        }
        new_owner_container = runtime.inspect_service("worker")
        new_owner_config = _object_mapping(new_owner_container.get("Config"))
        new_owner_labels = _object_mapping(new_owner_config.get("Labels"))
        new_owner_state = _object_mapping(new_owner_container.get("State"))
        if (
            new_owner_container.get("Id") != worker_container_id
            or new_owner_state.get("Status") != "running"
            or new_owner_labels.get("com.docker.compose.project") != runtime.stack.project
        ):
            raise AcceptanceFailure(
                "The new worker owner changed container identity or UUID ownership"
            )
        new_state: dict[str, dict[str, object]] = {}
        restart_metric_polls = 0
        restart_metric_poll_failures: Counter[str] = Counter()

        def observed_restart() -> bool:
            nonlocal new_state, restart_metric_polls
            restart_metric_polls += 1
            try:
                _names, new_state = runtime.worker_metric_state()
            except AcceptanceFailure as exc:
                restart_metric_poll_failures["acceptance_failure"] += 1
                ambiguous_role = re.fullmatch(
                    r"Prometheus returned multiple worker series for the ([a-z_]+) metric",
                    str(exc),
                )
                if ambiguous_role:
                    restart_metric_poll_failures["multiple_worker_series"] += 1
                worker_restart_context["latest_metric_poll"] = {
                    "poll_count": restart_metric_polls,
                    "poll_failure_counts": dict(restart_metric_poll_failures),
                    "query_available": False,
                    "failure_category": (
                        "multiple_worker_series" if ambiguous_role else "acceptance_failure"
                    ),
                    "ambiguous_metric_role": ambiguous_role.group(1) if ambiguous_role else None,
                    "query_diagnostic": runtime.last_worker_metric_query_diagnostic,
                }
                return False
            except (OSError, ValueError) as exc:
                restart_metric_poll_failures[type(exc).__name__] += 1
                worker_restart_context["latest_metric_poll"] = {
                    "poll_count": restart_metric_polls,
                    "poll_failure_counts": dict(restart_metric_poll_failures),
                    "query_available": False,
                    "failure_category": type(exc).__name__,
                    "query_diagnostic": runtime.last_worker_metric_query_diagnostic,
                }
                return False
            started_at = _worker_metric_value(new_state, "started")
            poll_summary = runtime.worker_restart_observation_summary(stopped_baseline, new_state)
            worker_restart_context["latest_metric_poll"] = {
                "poll_count": restart_metric_polls,
                "poll_failure_counts": dict(restart_metric_poll_failures),
                "query_available": True,
                "query_diagnostic": runtime.last_worker_metric_query_diagnostic,
                "available_roles": sorted(new_state),
                "started_advanced": poll_summary.get("started_advanced") is True,
                "series_identity_matches": (poll_summary.get("series_identity_matches") is True),
                "started_series_matches": poll_summary.get("started_series_matches") is True,
                "last_successful_scan_series_matches": (
                    poll_summary.get("last_successful_scan_series_matches") is True
                ),
                "last_progress_series_matches": (
                    poll_summary.get("last_progress_series_matches") is True
                ),
                "last_successful_scan_retained": (
                    poll_summary.get("last_successful_scan_retained") is True
                ),
                "last_progress_retained": poll_summary.get("last_progress_retained") is True,
                "successful_scans_available": "successful_scans" in new_state,
                "latest_values": {
                    name: poll_summary.get(name)
                    for name in (
                        "started_timestamp_seconds",
                        "last_successful_scan_timestamp_seconds",
                        "last_progress_timestamp_seconds",
                    )
                },
            }
            return (
                started_at is not None
                and started_at > old_started
                and "last_successful_scan" in new_state
                and "last_progress" in new_state
                and "successful_scans" in new_state
            )

        try:
            runtime.stack.wait_for(
                observed_restart,
                seconds=45,
                failure="The new worker start and cached same-slot points were not observed",
            )
        except AcceptanceFailure:
            try:
                worker_at_timeout = runtime.inspect_service("worker", timeout=10)
                mysql_at_timeout = runtime.inspect_service("mysql", timeout=10)
                worker_timeout_state = _object_mapping(worker_at_timeout.get("State"))
                mysql_timeout_state = _object_mapping(mysql_at_timeout.get("State"))
                worker_restart_context["container_state_at_timeout"] = {
                    "worker_same_container_running": (
                        worker_at_timeout.get("Id") == worker_container_id
                        and worker_timeout_state.get("Status") == "running"
                    ),
                    "mysql_same_container_exited": (
                        mysql_at_timeout.get("Id") == mysql_container_id
                        and mysql_timeout_state.get("Status") == "exited"
                    ),
                    "worker_status": worker_timeout_state.get("Status"),
                    "mysql_status": mysql_timeout_state.get("Status"),
                    "worker_restart_count": worker_at_timeout.get("RestartCount"),
                    "mysql_restart_count": mysql_at_timeout.get("RestartCount"),
                }
            except Exception as exc:
                worker_restart_context["container_state_at_timeout"] = {
                    "available": False,
                    "inspection_error_category": type(exc).__name__,
                }
            try:
                worker_records = runtime.stack.worker_records()
                latest_record = next(
                    (record for record in reversed(worker_records) if isinstance(record, dict)),
                    None,
                )
                if latest_record is None:
                    worker_restart_context["worker_log_state_at_timeout"] = {
                        "available": True,
                        "record_present": False,
                    }
                else:
                    worker_id_value = latest_record.get("worker_id")
                    valid_worker_id = (
                        worker_id_value
                        if isinstance(worker_id_value, str)
                        and re.fullmatch(r"[0-9a-f]{32}", worker_id_value)
                        else None
                    )
                    event_value = latest_record.get("event")
                    phase_value = latest_record.get("phase")
                    scans_value = latest_record.get("successful_scans")
                    worker_restart_context["worker_log_state_at_timeout"] = {
                        "available": True,
                        "record_present": True,
                        "event_class": (
                            event_value
                            if isinstance(event_value, str)
                            and event_value
                            in {
                                "starting",
                                "heartbeat",
                                "scan",
                                "candidate",
                                "ready",
                                "stopped",
                                "fatal",
                            }
                            else "other"
                        ),
                        "phase": (
                            phase_value
                            if isinstance(phase_value, str)
                            and re.fullmatch(r"[a-z_]{1,24}", phase_value)
                            else "other"
                        ),
                        "database_ok": (
                            latest_record.get("database_ok")
                            if type(latest_record.get("database_ok")) is bool
                            else None
                        ),
                        "successful_scans": (
                            scans_value
                            if type(scans_value) is int and 0 <= scans_value <= 1_000_000_000
                            else None
                        ),
                        "worker_identity_changed": (
                            valid_worker_id != old_owner_progress.worker_id
                            if valid_worker_id is not None
                            else None
                        ),
                    }
            except Exception as exc:
                worker_restart_context["worker_log_state_at_timeout"] = {
                    "available": False,
                    "error_category": type(exc).__name__,
                }
            raise
        restart_summary = runtime.worker_restart_observation_summary(stopped_baseline, new_state)
        failure_context["worker_restart_observations"] = restart_summary
        if not runtime.worker_restart_retained_prior_points(restart_summary):
            raise AcceptanceFailure(
                "Collector did not retain the same worker metric series and values across restart"
            )
        worker_after_observation = runtime.inspect_service("worker")
        mysql_after_observation = runtime.inspect_service("mysql")
        worker_after_state = _object_mapping(worker_after_observation.get("State"))
        mysql_after_state = _object_mapping(mysql_after_observation.get("State"))
        if (
            worker_after_observation.get("Id") != worker_container_id
            or worker_after_state.get("Status") != "running"
            or mysql_after_observation.get("Id") != mysql_container_id
            or mysql_after_state.get("Status") != "exited"
        ):
            raise AcceptanceFailure(
                "MySQL did not remain stopped through the same-slot worker observation"
            )
        new_owner_started = _worker_metric_value(new_state, "started")
        if new_owner_started is None:
            raise AcceptanceFailure("The new worker start timestamp was not numeric")
        worker_start_time = new_owner_state.get("StartedAt")
        if not isinstance(worker_start_time, str):
            raise AcceptanceFailure("The restarted worker omitted its actual start time")
        try:
            worker_start_at_utc = normalize_alertmanager_timestamp(worker_start_time)
            progress_stale_deadline = MonotonicDeadline.from_utc_timestamp(worker_start_time, 180)
        except ValueError:
            raise AcceptanceFailure("The restarted worker start time was invalid") from None
        initial_new_heartbeat = _worker_metric_value(new_state, "heartbeat")
        if initial_new_heartbeat is None:
            raise AcceptanceFailure("The restarted worker omitted its current-process heartbeat")

        def advancing_worker_heartbeat(request_deadline: float) -> object:
            _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
            started = _worker_metric_value(current, "started")
            heartbeat = _worker_metric_value(current, "heartbeat")
            heartbeat_sample = _object_mapping(current.get("heartbeat"))
            heartbeat_labels = _object_mapping(heartbeat_sample.get("labels"))
            if (
                started != new_owner_started
                or heartbeat is None
                or heartbeat <= initial_new_heartbeat
                or heartbeat < new_owner_started
                or time.time() - heartbeat > 45
                or heartbeat_labels.get("worker_slot") != "1"
            ):
                return None
            return current

        new_state = cast(
            dict[str, dict[str, object]],
            wait_until_deadline(
                progress_stale_deadline,
                advancing_worker_heartbeat,
                failure="The restarted worker heartbeat did not advance in its new process",
                interval_seconds=5,
            ),
        )
        heartbeat_stale_firing = heartbeat_stale_episode.get("firing")
        if not isinstance(heartbeat_stale_firing, dict):
            raise AcceptanceFailure("The stopped worker did not produce its stale heartbeat alert")

        def restarted_worker_heartbeat_is_fresh(request_deadline: float) -> bool:
            _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
            started = _worker_metric_value(current, "started")
            heartbeat = _worker_metric_value(current, "heartbeat")
            heartbeat_labels = _object_mapping(
                _object_mapping(current.get("heartbeat")).get("labels")
            )
            inspected = runtime.inspect_service("worker", deadline=request_deadline)
            inspected_state = _object_mapping(inspected.get("State"))
            return (
                started == new_owner_started
                and heartbeat is not None
                and heartbeat >= new_owner_started
                and time.time() - heartbeat <= 45
                and heartbeat_labels.get("worker_slot") == "1"
                and inspected.get("Id") == worker_container_id
                and inspected_state.get("Status") == "running"
            )

        heartbeat_recovery_deadline = MonotonicDeadline.from_utc_timestamp(worker_start_time, 90)
        heartbeat_recovery = wait_for_worker_alert_resolution(
            "WorkerHeartbeatStale",
            heartbeat_stale_firing,
            worker_alert_resolution_baselines["WorkerHeartbeatStale"],
            heartbeat_recovery_deadline,
            restarted_worker_heartbeat_is_fresh,
        )
        heartbeat_stale_episode["recovery"] = heartbeat_recovery
        worker_alert_evidence["WorkerHeartbeatStale"] = heartbeat_stale_episode

        progress_stale_firing = wait_for_worker_alert_firing(
            "WorkerProgressStale",
            worker_alert_firing_baselines["WorkerProgressStale"],
            progress_stale_deadline,
        )
        backlog_snapshot_stale_firing = wait_for_worker_alert_firing(
            "WorkerBacklogSnapshotStale",
            worker_alert_firing_baselines["WorkerBacklogSnapshotStale"],
            progress_stale_deadline,
        )
        stale_process_state = runtime.worker_metric_state(
            deadline=progress_stale_deadline.expires_at
        )[1]
        stale_started = _worker_metric_value(stale_process_state, "started")
        stale_heartbeat = _worker_metric_value(stale_process_state, "heartbeat")
        stale_progress = _worker_metric_value(stale_process_state, "last_progress")
        stale_snapshot = _worker_metric_value(stale_process_state, "last_successful_scan")
        if (
            stale_started != new_owner_started
            or stale_heartbeat is None
            or stale_heartbeat <= initial_new_heartbeat
            or time.time() - stale_heartbeat > 45
            or stale_progress is None
            or stale_progress >= new_owner_started
            or stale_snapshot is None
            or stale_snapshot >= new_owner_started
        ):
            raise AcceptanceFailure(
                "The worker did not keep advancing heartbeats while process progress and backlog "
                "snapshot stayed stale"
            )
        worker_alert_resolution_baselines["WorkerProgressStale"] = worker_receiver_snapshot(
            "WorkerProgressStale"
        )
        worker_alert_resolution_baselines["WorkerBacklogSnapshotStale"] = worker_receiver_snapshot(
            "WorkerBacklogSnapshotStale"
        )
        progress_stale_evidence: dict[str, object] = {
            "stimulus": {
                "worker_started_at_utc": worker_start_at_utc,
                "worker_container_id_matches": True,
                "mysql_unavailable": True,
                "worker_slot": "1",
            },
            "heartbeat_advanced_and_fresh": True,
            "progress_timestamp_remained_before_process_start": True,
            "backlog_snapshot_timestamp_remained_before_process_start": True,
            "firing": progress_stale_firing,
        }
        backlog_snapshot_stale_evidence: dict[str, object] = {
            "stimulus": {
                "worker_started_at_utc": worker_start_at_utc,
                "worker_container_id_matches": True,
                "mysql_unavailable": True,
                "worker_slot": "1",
            },
            "heartbeat_advanced_and_fresh": True,
            "progress_timestamp_remained_before_process_start": True,
            "backlog_snapshot_timestamp_remained_before_process_start": True,
            "firing": backlog_snapshot_stale_firing,
        }
        worker_alert_evidence["WorkerProgressStale"] = progress_stale_evidence
        worker_alert_evidence["WorkerBacklogSnapshotStale"] = backlog_snapshot_stale_evidence
        evidence["same_slot_restart"] = {
            "slot": "1",
            **restart_summary,
            "counter_name": selected_names.get("successful_scans"),
            "new_owner_started_generation_pinned": True,
        }
        acceptance_success(
            runtime.compose("up", "-d", "--wait", "--wait-timeout", "120", "mysql", timeout=150),
            "The UUID-owned database did not recover after the observation",
        )
        recovery_scan_before, recovery_scan_after = business_probe._wait_for_later_worker_scan(
            excluded_worker_id=old_owner_progress.worker_id,
        )
        new_owner_worker_id = recovery_scan_before.worker_id
        stopped_scan_timestamp = _worker_metric_value(stopped_baseline, "last_successful_scan")
        stopped_progress_timestamp = _worker_metric_value(stopped_baseline, "last_progress")
        if stopped_scan_timestamp is None or stopped_progress_timestamp is None:
            raise AcceptanceFailure("The stopped worker omitted its DB-backed scan baseline")
        recovered_owner_state: dict[str, dict[str, object]] = {}

        def new_owner_scan_and_progress_after_database_recovery() -> bool:
            nonlocal recovered_owner_state
            _names, recovered_owner_state = runtime.worker_metric_state()
            started_at = _worker_metric_value(recovered_owner_state, "started")
            successful_scan_at = _worker_metric_value(recovered_owner_state, "last_successful_scan")
            progress_at = _worker_metric_value(recovered_owner_state, "last_progress")
            recovery_summary = runtime.worker_restart_observation_summary(
                stopped_baseline, recovered_owner_state
            )
            return (
                started_at == new_owner_started
                and recovery_summary.get("series_identity_matches") is True
                and successful_scan_at is not None
                and successful_scan_at > stopped_scan_timestamp
                and progress_at is not None
                # This gauge may use DB scan time or worker event time; compare change only.
                and progress_at != stopped_progress_timestamp
            )

        runtime.stack.wait_for(
            new_owner_scan_and_progress_after_database_recovery,
            seconds=60,
            failure=(
                "The new worker owner did not complete a successful database scan and real "
                "progress after MySQL recovery"
            ),
        )
        recovered_scan_timestamp = _worker_metric_value(
            recovered_owner_state, "last_successful_scan"
        )
        recovered_progress_timestamp = _worker_metric_value(recovered_owner_state, "last_progress")
        if recovered_scan_timestamp is None or recovered_progress_timestamp is None:
            raise AcceptanceFailure("The new owner omitted its recovered scan/progress timestamps")

        def recovered_worker_snapshot_is_fresh(request_deadline: float) -> bool:
            _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
            started = _worker_metric_value(current, "started")
            heartbeat = _worker_metric_value(current, "heartbeat")
            scan = _worker_metric_value(current, "last_successful_scan")
            progress = _worker_metric_value(current, "last_progress")
            snapshot_samples = prometheus_vector(
                "prl:worker:backlog_snapshot_fresh", request_deadline
            )
            fresh_snapshot_samples = worker_alert_samples(snapshot_samples, worker_slot="1")
            return (
                started == new_owner_started
                and heartbeat is not None
                and 0 <= time.time() - heartbeat <= 45
                and scan is not None
                and scan > stopped_scan_timestamp
                and progress is not None
                and progress != stopped_progress_timestamp
                and bool(fresh_snapshot_samples)
            )

        worker_recovery_deadline = MonotonicDeadline.after(120)
        progress_stale_evidence["recovery"] = wait_for_worker_alert_resolution(
            "WorkerProgressStale",
            progress_stale_firing,
            worker_alert_resolution_baselines["WorkerProgressStale"],
            worker_recovery_deadline,
            recovered_worker_snapshot_is_fresh,
        )
        backlog_snapshot_stale_evidence["recovery"] = wait_for_worker_alert_resolution(
            "WorkerBacklogSnapshotStale",
            backlog_snapshot_stale_firing,
            worker_alert_resolution_baselines["WorkerBacklogSnapshotStale"],
            worker_recovery_deadline,
            recovered_worker_snapshot_is_fresh,
        )
        recovery_appointment_id = recovery_fixture.get("appointment_id")
        recovery_proposal_id = recovery_fixture.get("proposal_id")
        if not isinstance(recovery_appointment_id, str) or not isinstance(
            recovery_proposal_id, str
        ):
            raise AcceptanceFailure("The worker recovery fixture omitted its business identifiers")
        runtime.stack.wait_for(
            lambda: (
                runtime.stack.sql(
                    "SELECT CONCAT(cp.status, ':', a.status, ':', COALESCE(a.cancel_reason, '-')) "
                    "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
                    f"WHERE cp.id = UNHEX(REPLACE('{str(UUID(recovery_proposal_id))}', '-', ''));"
                ).strip()
                == "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED"
            ),
            seconds=30,
            failure="The new owner did not expire the persisted proposal after MySQL recovery",
        )
        recovery_effects = business_probe._business_side_effects(
            recovery_appointment_id, recovery_proposal_id, expired=True
        )
        expiry_followup_scan_before, expiry_followup_scan_after = (
            business_probe._wait_for_later_worker_scan(
                excluded_worker_id=old_owner_progress.worker_id,
                expected_worker_id=new_owner_worker_id,
            )
        )
        if (
            business_probe._business_side_effects(
                recovery_appointment_id, recovery_proposal_id, expired=True
            )
            != recovery_effects
        ):
            raise AcceptanceFailure(
                "Post-recovery expiry repeated its audit or notification effects"
            )
        evidence["new_owner_database_recovery"] = {
            "started_timestamp_seconds": new_owner_started,
            "last_successful_scan_timestamp_seconds": recovered_scan_timestamp,
            "last_progress_timestamp_seconds": recovered_progress_timestamp,
            "first_healthy_scan_counter_after_recovery": recovery_scan_before.successful_scans,
            "later_healthy_scan_counter_after_recovery": recovery_scan_after.successful_scans,
            "scan_counter_before_expiry_followup": expiry_followup_scan_before.successful_scans,
            "later_scan_counter_after_expiry": expiry_followup_scan_after.successful_scans,
            "worker_process_changed_from_pre_restart": (
                recovery_scan_before.worker_id != old_owner_progress.worker_id
            ),
            "started_generation_unchanged_after_recovery": (
                _worker_metric_value(recovered_owner_state, "started") == new_owner_started
            ),
            "series_identity_matches_stopped_baseline": (
                runtime.worker_restart_observation_summary(
                    stopped_baseline, recovered_owner_state
                ).get("series_identity_matches")
                is True
            ),
            "scan_advanced_since_stopped_db_baseline": True,
            "progress_metric_changed_since_stopped_baseline": (
                recovered_progress_timestamp != stopped_progress_timestamp
            ),
            "counter_advanced_in_same_new_owner_process": (
                recovery_scan_after.successful_scans > recovery_scan_before.successful_scans
                and expiry_followup_scan_after.successful_scans
                > expiry_followup_scan_before.successful_scans
                and expiry_followup_scan_before.worker_id == new_owner_worker_id
                and expiry_followup_scan_after.worker_id == new_owner_worker_id
            ),
            "persisted_due_proposal_expired_by_new_owner": True,
            "new_owner_container_identity_preserved": True,
            "audit_event_count": recovery_effects["audit_event_count"],
            "notification_count": recovery_effects["notification_count"],
            "duplicate_effects_after_followup_scan": False,
        }
        if old_volumes != runtime.volume_snapshot():
            raise AcceptanceFailure(
                "A persistent volume identity changed across worker/database restart"
            )

        failure_context["stage"] = "worker_backlog_overdue_row_lock"
        overdue_receiver_baseline = worker_receiver_snapshot("WorkerBacklogOverdue")
        overdue_fixture = business_probe.prepare_due_expiry_fixture()
        overdue_appointment_id = overdue_fixture.get("appointment_id")
        overdue_proposal_id = overdue_fixture.get("proposal_id")
        overdue_worker_id = overdue_fixture.get("worker_container_id")
        if (
            not isinstance(overdue_appointment_id, str)
            or not isinstance(overdue_proposal_id, str)
            or not isinstance(overdue_worker_id, str)
            or overdue_worker_id != worker_container_id
        ):
            raise AcceptanceFailure("The backlog overdue fixture lost its owned worker identity")
        overdue_stopped_container = runtime.inspect_service("worker")
        overdue_stopped_state = _object_mapping(overdue_stopped_container.get("State"))
        overdue_stopped_labels = _object_mapping(
            _object_mapping(overdue_stopped_container.get("Config")).get("Labels")
        )
        overdue_finished_at = overdue_stopped_state.get("FinishedAt")
        if (
            overdue_stopped_container.get("Id") != worker_container_id
            or overdue_stopped_state.get("Status") != "exited"
            or overdue_stopped_labels.get("com.docker.compose.project") != runtime.stack.project
            or not isinstance(overdue_finished_at, str)
            or overdue_finished_at.startswith("0001-")
        ):
            raise AcceptanceFailure("The overdue worker stop did not preserve container ownership")
        try:
            overdue_stop_at_utc = normalize_alertmanager_timestamp(overdue_finished_at)
            overdue_deadline = MonotonicDeadline.from_utc_timestamp(overdue_finished_at, 220)
        except ValueError:
            raise AcceptanceFailure(
                "The overdue worker stop exceeded its bounded firing window"
            ) from None

        def start_same_worker_for_overdue_probe() -> str:
            acceptance_success(
                runtime.compose("up", "-d", "--no-deps", "--no-recreate", "worker", timeout=45),
                "The same worker container did not start behind the held proposal row lock",
            )
            started_record = runtime.inspect_service("worker")
            started_state = _object_mapping(started_record.get("State"))
            started_labels = _object_mapping(
                _object_mapping(started_record.get("Config")).get("Labels")
            )
            started_at = started_state.get("StartedAt")
            if (
                started_record.get("Id") != worker_container_id
                or started_state.get("Status") != "running"
                or started_labels.get("com.docker.compose.project") != runtime.stack.project
                or not isinstance(started_at, str)
            ):
                raise AcceptanceFailure("The overdue worker did not retain its owned container")
            return started_at

        with acquire_pending_proposal_row_lock(
            runtime.stack.base,
            runtime.stack.env,
            lambda: runtime.inspect_service("mysql"),
            compose_project=runtime.stack.project,
            mysql_container_id=mysql_container_id,
            proposal_id=overdue_proposal_id,
        ) as proposal_row_lock:
            stopped_overdue_metrics = runtime.worker_metric_state(
                deadline=overdue_deadline.expires_at
            )[1]
            stopped_overdue_started = _worker_metric_value(stopped_overdue_metrics, "started")
            if stopped_overdue_started is None:
                raise AcceptanceFailure("The stopped worker omitted its overdue alert baseline")
            overdue_worker_started_at = start_same_worker_for_overdue_probe()
            overdue_started_at_utc = normalize_alertmanager_timestamp(overdue_worker_started_at)
            overdue_start_metrics: dict[str, dict[str, object]] = {}

            def overdue_worker_started_with_fresh_heartbeat(request_deadline: float) -> object:
                nonlocal overdue_start_metrics
                _metric_names, candidate = runtime.worker_metric_state(deadline=request_deadline)
                started = _worker_metric_value(candidate, "started")
                heartbeat = _worker_metric_value(candidate, "heartbeat")
                heartbeat_labels = _object_mapping(
                    _object_mapping(candidate.get("heartbeat")).get("labels")
                )
                parsed_container_start = datetime.fromisoformat(
                    overdue_worker_started_at.replace("Z", "+00:00")
                ).timestamp()
                if (
                    started is None
                    or started <= stopped_overdue_started
                    or not runtime.worker_started_value_matches_container_generation(
                        started, parsed_container_start
                    )
                    or heartbeat is None
                    or heartbeat < started
                    or not 0 <= time.time() - heartbeat <= 45
                    or heartbeat_labels.get("worker_slot") != "1"
                ):
                    return None
                overdue_start_metrics = candidate
                return candidate

            wait_until_deadline(
                overdue_deadline,
                overdue_worker_started_with_fresh_heartbeat,
                failure="The overdue worker did not publish its fresh same-slot heartbeat",
                interval_seconds=5,
            )
            overdue_started_metric = _worker_metric_value(overdue_start_metrics, "started")
            overdue_initial_scan = _worker_metric_value(
                overdue_start_metrics, "last_successful_scan"
            )
            if overdue_started_metric is None or overdue_initial_scan is None:
                raise AcceptanceFailure("The overdue worker omitted fresh scan metrics")
            last_fresh_scan = overdue_initial_scan
            fresh_scan_advancements = 0
            latest_overdue_metrics: dict[str, float] = {}

            def fresh_overdue_worker_discovery(request_deadline: float) -> object:
                nonlocal last_fresh_scan, fresh_scan_advancements, latest_overdue_metrics
                if not proposal_row_lock.is_open:
                    return None
                _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
                started = _worker_metric_value(current, "started")
                heartbeat = _worker_metric_value(current, "heartbeat")
                scan = _worker_metric_value(current, "last_successful_scan")
                backlog_count = _worker_metric_value(current, "backlog_count")
                overdue_seconds = _worker_metric_value(current, "oldest_overdue")
                heartbeat_labels = _object_mapping(
                    _object_mapping(current.get("heartbeat")).get("labels")
                )
                if (
                    started != overdue_started_metric
                    or heartbeat is None
                    or not 0 <= time.time() - heartbeat <= 45
                    or heartbeat_labels.get("worker_slot") != "1"
                    or scan is None
                    or scan < started
                    or not 0 <= time.time() - scan <= 90
                    or backlog_count is None
                    or backlog_count <= 0
                    or overdue_seconds is None
                    or overdue_seconds <= 60
                ):
                    return None
                if scan > last_fresh_scan:
                    fresh_scan_advancements += 1
                    last_fresh_scan = scan
                latest_overdue_metrics = {
                    "started_timestamp_seconds": started,
                    "heartbeat_timestamp_seconds": heartbeat,
                    "last_successful_scan_timestamp_seconds": scan,
                    "backlog_count": backlog_count,
                    "oldest_overdue_seconds": overdue_seconds,
                    "fresh_scan_advancements_while_locked": float(fresh_scan_advancements),
                }
                if fresh_scan_advancements < 2:
                    return None
                return latest_overdue_metrics

            def overdue_pending_observed(request_deadline: float) -> object:
                metric_observation = fresh_overdue_worker_discovery(request_deadline)
                if metric_observation is None:
                    return None
                predicate_samples = worker_alert_samples(
                    prometheus_vector(
                        worker_alert_expressions["WorkerBacklogOverdue"], request_deadline
                    ),
                    worker_slot=None,
                )
                pending_samples = worker_alert_samples(
                    prometheus_vector(
                        'ALERTS{alertname="WorkerBacklogOverdue",alertstate="pending"}',
                        request_deadline,
                    ),
                    worker_slot=None,
                )
                if not predicate_samples or not pending_samples:
                    return None
                return {
                    "prometheus_predicate_sample_count": len(predicate_samples),
                    "prometheus_pending_sample_count": len(pending_samples),
                    "metrics": metric_observation,
                    "observed_elapsed_seconds": round(overdue_deadline.elapsed, 3),
                }

            pending_observation = wait_until_deadline(
                overdue_deadline,
                overdue_pending_observed,
                failure=(
                    "Prometheus did not expose WorkerBacklogOverdue as pending while the row "
                    "was locked"
                ),
                interval_seconds=5,
            )
            if not isinstance(pending_observation, dict):
                raise AcceptanceFailure("The overdue alert pending observation was malformed")
            overdue_firing = wait_for_worker_alert_firing(
                "WorkerBacklogOverdue",
                overdue_receiver_baseline,
                overdue_deadline,
                stimulus_check=fresh_overdue_worker_discovery,
            )
            sql_id = f"UNHEX(REPLACE('{str(UUID(overdue_proposal_id))}', '-', ''))"
            remaining_sql_budget = min(10.0, overdue_deadline.remaining)
            if remaining_sql_budget < 1:
                raise AcceptanceFailure("The overdue alert deadline expired before row validation")
            pending_row = run_bounded_mysql_statement(
                runtime.stack.base,
                runtime.stack.env,
                "SELECT CONCAT(status, ':', IF(expires_at > created_at, 1, 0), ':', "
                "TIMESTAMPDIFF(MICROSECOND, expires_at, UTC_TIMESTAMP(6)) / 1000000) "
                f"FROM change_proposals WHERE id = {sql_id};",
                timeout_seconds=max(1.0, remaining_sql_budget),
            )
            pending_parts = pending_row.split(":")
            if len(pending_parts) != 3 or pending_parts[0] != "PENDING" or pending_parts[1] != "1":
                raise AcceptanceFailure("The overdue proposal expired or corrupted before unlock")
            try:
                overdue_age_at_firing = float(pending_parts[2])
            except ValueError:
                raise AcceptanceFailure("The naturally due proposal age was invalid") from None
            if overdue_age_at_firing <= 60 or not proposal_row_lock.is_open:
                raise AcceptanceFailure(
                    "The overdue proposal lock or natural expiry interval was lost"
                )
            overdue_alert_evidence: dict[str, object] = {
                "stimulus": {
                    "worker_stopped_at_utc": overdue_stop_at_utc,
                    "worker_container_id_matches": True,
                    "mysql_container_id_matches": True,
                    "mysql_running_and_compose_owned": True,
                    "proposal_remained_pending": True,
                    "expires_at_remained_after_created_at": True,
                    "proposal_age_at_firing_seconds": round(overdue_age_at_firing, 3),
                    "row_lock_ready_marker_observed": proposal_row_lock.is_open,
                    "row_lock_commit_marker_pending": True,
                },
                "worker_started_at_utc": overdue_started_at_utc,
                "prometheus_pending": pending_observation,
                "firing": overdue_firing,
            }
            worker_alert_evidence["WorkerBacklogOverdue"] = overdue_alert_evidence
            overdue_resolution_receiver_baseline = capture_receiver_snapshot_before_recovery(
                lambda: worker_receiver_snapshot("WorkerBacklogOverdue"),
                proposal_row_lock.commit_and_release,
            )
            overdue_recovery_deadline = MonotonicDeadline.after(120)

            def overdue_business_state(request_deadline: float) -> str:
                return run_bounded_mysql_statement(
                    runtime.stack.base,
                    runtime.stack.env,
                    "SELECT CONCAT(cp.status, ':', a.status, ':', COALESCE(a.cancel_reason, '-')) "
                    "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
                    f"WHERE cp.id = {sql_id};",
                    timeout_seconds=max(1.0, min(10.0, request_deadline - time.monotonic())),
                )

            def overdue_recovery_observed(request_deadline: float) -> object:
                _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
                started = _worker_metric_value(current, "started")
                heartbeat = _worker_metric_value(current, "heartbeat")
                scan = _worker_metric_value(current, "last_successful_scan")
                progress = _worker_metric_value(current, "last_progress")
                backlog_count = _worker_metric_value(current, "backlog_count")
                scans = _worker_metric_value(current, "successful_scans")
                snapshot_samples = worker_alert_samples(
                    prometheus_vector("prl:worker:backlog_snapshot_fresh", request_deadline),
                    worker_slot="1",
                )
                state = overdue_business_state(request_deadline)
                current_container = runtime.inspect_service("worker", deadline=request_deadline)
                current_state = _object_mapping(current_container.get("State"))
                if (
                    started != overdue_started_metric
                    or heartbeat is None
                    or not 0 <= time.time() - heartbeat <= 45
                    or scan is None
                    or scan <= overdue_initial_scan
                    or not 0 <= time.time() - scan <= 90
                    or progress is None
                    or backlog_count != 0
                    or not snapshot_samples
                    or scans is None
                    or current_container.get("Id") != worker_container_id
                    or current_state.get("Status") != "running"
                    or state != "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED"
                ):
                    return None
                return {
                    "worker_started_timestamp_seconds": started,
                    "last_successful_scan_timestamp_seconds": scan,
                    "last_progress_timestamp_seconds": progress,
                    "successful_scans_total": scans,
                    "backlog_count": backlog_count,
                    "worker_container_identity_preserved": True,
                    "proposal_expired_by_worker": True,
                }

            overdue_resolved = wait_for_worker_alert_resolution(
                "WorkerBacklogOverdue",
                overdue_firing,
                overdue_resolution_receiver_baseline,
                overdue_recovery_deadline,
                lambda request_deadline: bool(overdue_recovery_observed(request_deadline)),
            )

        overdue_appointment_uuid = str(UUID(overdue_appointment_id))
        overdue_proposal_uuid = str(UUID(overdue_proposal_id))
        overdue_effects_sql = (
            "SELECT CONCAT(cp.status, ':', a.status, ':', COALESCE(a.cancel_reason, '-')) "
            "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
            f"WHERE cp.id = {sql_id};\n"
            "SELECT COUNT(*) FROM audit_events "
            "WHERE action = 'APPOINTMENT_CREATED' AND entity_type = 'APPOINTMENT' "
            f"AND entity_id = '{overdue_appointment_uuid}';\n"
            "SELECT COUNT(*) FROM audit_events "
            "WHERE action = 'CHANGE_PROPOSAL_CREATED' AND entity_type = 'CHANGE_PROPOSAL' "
            f"AND entity_id = '{overdue_proposal_uuid}';\n"
            "SELECT COUNT(*) FROM audit_events "
            "WHERE action = 'CHANGE_PROPOSAL_EXPIRED' AND entity_type = 'CHANGE_PROPOSAL' "
            f"AND entity_id = '{overdue_proposal_uuid}';\n"
            "SELECT COUNT(*) FROM notifications "
            f"WHERE appointment_id = UNHEX(REPLACE('{overdue_appointment_uuid}', '-', '')) "
            "AND event_type = 'APPOINTMENT_CREATED';\n"
            "SELECT COUNT(*) FROM notifications "
            f"WHERE appointment_id = UNHEX(REPLACE('{overdue_appointment_uuid}', '-', '')) "
            "AND event_type = 'CHANGE_PROPOSAL_CREATED';\n"
            "SELECT COUNT(*) FROM notifications "
            f"WHERE appointment_id = UNHEX(REPLACE('{overdue_appointment_uuid}', '-', '')) "
            "AND event_type = 'CHANGE_PROPOSAL_EXPIRED';"
        )

        overdue_business_deadline = MonotonicDeadline.after(45)

        def overdue_business_effects(deadline: MonotonicDeadline) -> tuple[str, ...]:
            remaining = deadline.remaining
            if remaining < 1:
                raise AcceptanceFailure("The overdue business verification deadline expired")
            output = run_bounded_mysql_statement(
                runtime.stack.base,
                runtime.stack.env,
                overdue_effects_sql,
                timeout_seconds=min(10.0, remaining),
            )
            lines = tuple(output.splitlines())
            if lines != (
                "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
                "1",
                "1",
                "1",
                "2",
                "2",
                "2",
            ):
                raise AcceptanceFailure(
                    "The overdue proposal did not retain exactly one expiry audit and two "
                    "notifications"
                )
            return lines

        overdue_effects = overdue_business_effects(overdue_business_deadline)
        latest_overdue_metrics = runtime.worker_metric_state(
            deadline=overdue_business_deadline.expires_at
        )[1]
        first_overdue_scan_total = _worker_metric_value(latest_overdue_metrics, "successful_scans")
        if first_overdue_scan_total is None:
            raise AcceptanceFailure("The overdue worker omitted its successful scan counter")

        def later_overdue_scan(request_deadline: float) -> object:
            _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
            scans = _worker_metric_value(current, "successful_scans")
            started = _worker_metric_value(current, "started")
            container = runtime.inspect_service("worker", deadline=request_deadline)
            container_state = _object_mapping(container.get("State"))
            if (
                scans is None
                or scans <= first_overdue_scan_total
                or started != overdue_started_metric
                or container.get("Id") != worker_container_id
                or container_state.get("Status") != "running"
            ):
                return None
            return scans

        wait_until_deadline(
            overdue_business_deadline,
            later_overdue_scan,
            failure="The same overdue worker process did not complete a later scan after recovery",
            interval_seconds=5,
        )
        if overdue_business_effects(overdue_business_deadline) != overdue_effects:
            raise AcceptanceFailure("A later worker scan duplicated overdue proposal side effects")
        overdue_alert_evidence.update(
            {
                "recovery": overdue_resolved,
                "business_invariants": {
                    "audit_expired_cancelled_reason": overdue_effects[0],
                    "appointment_created_audits": int(overdue_effects[1]),
                    "proposal_created_audits": int(overdue_effects[2]),
                    "proposal_expired_audits": int(overdue_effects[3]),
                    "appointment_created_notifications": int(overdue_effects[4]),
                    "proposal_created_notifications": int(overdue_effects[5]),
                    "proposal_expired_notifications": int(overdue_effects[6]),
                    "later_scan_duplicated_effects": False,
                },
            }
        )
        if old_volumes != runtime.volume_snapshot():
            raise AcceptanceFailure("Backlog alert recovery changed a persistent volume identity")

        failure_context["stage"] = "worker_heartbeat_missing_collector_reset"
        missing_receiver_baseline = worker_receiver_snapshot("WorkerHeartbeatMissing")
        missing_worker_before = runtime.inspect_service("worker")
        missing_worker_before_state = _object_mapping(missing_worker_before.get("State"))
        if (
            missing_worker_before.get("Id") != worker_container_id
            or missing_worker_before_state.get("Status") != "running"
        ):
            raise AcceptanceFailure("The heartbeat-missing phase lost the running worker container")
        missing_worker_metrics_before = runtime.worker_metric_state()[1]
        missing_worker_started_before = _worker_metric_value(
            missing_worker_metrics_before, "started"
        )
        if missing_worker_started_before is None:
            raise AcceptanceFailure("The heartbeat-missing phase omitted the worker baseline")
        acceptance_success(
            runtime.compose("stop", "--timeout", "10", "worker", timeout=30),
            "The UUID-owned worker could not stop before the final Collector reset",
        )
        missing_worker_stopped = runtime.inspect_service("worker")
        missing_worker_stopped_state = _object_mapping(missing_worker_stopped.get("State"))
        missing_worker_stopped_labels = _object_mapping(
            _object_mapping(missing_worker_stopped.get("Config")).get("Labels")
        )
        missing_worker_finished_at = missing_worker_stopped_state.get("FinishedAt")
        if (
            missing_worker_stopped.get("Id") != worker_container_id
            or missing_worker_stopped_state.get("Status") != "exited"
            or missing_worker_stopped_labels.get("com.docker.compose.project")
            != runtime.stack.project
            or not isinstance(missing_worker_finished_at, str)
            or missing_worker_finished_at.startswith("0001-")
        ):
            raise AcceptanceFailure("The stopped heartbeat-missing worker lost UUID ownership")
        missing_worker_exit_at_utc: str | None = None
        missing_worker_exit_epoch: float | None = None
        try:
            missing_worker_exit_at_utc = normalize_alertmanager_timestamp(
                missing_worker_finished_at
            )
            parsed_worker_exit = datetime.fromisoformat(
                missing_worker_finished_at.replace("Z", "+00:00")
            )
            if parsed_worker_exit.tzinfo is None or parsed_worker_exit.utcoffset() is None:
                raise ValueError("The stopped worker exit timestamp was invalid")
            missing_worker_exit_epoch = _validated_epoch_seconds(
                parsed_worker_exit.timestamp()
            )
            if missing_worker_exit_epoch is None:
                raise ValueError("The stopped worker exit timestamp was invalid")
            missing_transition_deadline = MonotonicDeadline.from_utc_timestamp(
                missing_worker_finished_at, 90
            )
        except ValueError as error:
            deadline_expired_at_capture = str(error) == "The event deadline has already expired"
            failure_context["worker_heartbeat_missing_firing_budget"] = {
                "worker_exit_at_utc": missing_worker_exit_at_utc,
                "budget_seconds": 90,
                "expired": deadline_expired_at_capture,
                "deadline_error": (
                    "expired_at_capture"
                    if deadline_expired_at_capture
                    else "invalid_worker_exit_timestamp"
                ),
            }
            if deadline_expired_at_capture:
                raise AcceptanceFailure(
                    "The WorkerHeartbeatMissing 90-second firing budget had already expired "
                    "at worker stop"
                ) from None
            raise AcceptanceFailure("The stopped worker exit timestamp was invalid") from None

        def record_missing_worker_deadline(stage: str) -> float:
            remaining = missing_transition_deadline.remaining
            failure_context["worker_heartbeat_missing_firing_budget"] = {
                "worker_exit_at_utc": missing_worker_exit_at_utc,
                "budget_seconds": 90,
                "stage": stage,
                "elapsed_seconds": round(missing_transition_deadline.elapsed, 3),
                "remaining_seconds": round(remaining, 3),
                "expired": remaining <= 0,
            }
            return remaining

        def require_missing_worker_deadline(stage: str) -> None:
            if record_missing_worker_deadline(stage) <= 0:
                raise AcceptanceFailure(
                    f"The WorkerHeartbeatMissing 90-second firing budget expired during {stage}"
                )

        failure_context["stage"] = "collector_restart_command"
        require_missing_worker_deadline("before_collector_restart")
        collector_before_restart = runtime.inspect_service(
            "collector", timeout=5, deadline=missing_transition_deadline.expires_at
        )
        collector_started_before_restart_epoch = post_restart_container_started_epoch(
            collector_before_restart,
            expected_container_id=collector_id_before,
            expected_project=runtime.stack.project,
        )
        if collector_started_before_restart_epoch is None:
            raise AcceptanceFailure(
                "The UUID-owned Collector did not expose its running pre-restart generation"
            )
        try:
            acceptance_success(
                runtime.compose(
                    "restart",
                    "--timeout",
                    "20",
                    "collector",
                    timeout=45,
                    deadline=missing_transition_deadline.expires_at,
                ),
                "The UUID-owned Collector could not restart",
            )
        except Exception:
            record_missing_worker_deadline("collector_restart_command_failed")
            raise
        require_missing_worker_deadline("collector_restart_command_completed")
        collector_after_restart = runtime.inspect_service(
            "collector", timeout=5, deadline=missing_transition_deadline.expires_at
        )
        collector_restart_epoch = post_restart_container_started_epoch(
            collector_after_restart,
            expected_container_id=collector_id_before,
            expected_project=runtime.stack.project,
            later_than_epoch=collector_started_before_restart_epoch,
        )
        if collector_restart_epoch is None:
            raise AcceptanceFailure(
                "The UUID-owned Collector did not expose a newer running start time after restart"
            )
        require_missing_worker_deadline("collector_restart_inspected")

        failure_context["stage"] = "collector_restart_target_matrix"
        try:
            runtime.wait_targets(deadline=missing_transition_deadline.expires_at)
        except Exception:
            record_missing_worker_deadline("collector_target_wait_failed")
            raise
        require_missing_worker_deadline("collector_target_wait_completed")

        failure_context["stage"] = "collector_restart_volume_identity"
        try:
            if old_volumes != runtime.volume_snapshot(
                deadline=missing_transition_deadline.expires_at
            ):
                raise AcceptanceFailure("Collector restart changed a persistent volume identity")
        except Exception:
            record_missing_worker_deadline("collector_volume_check_failed")
            raise
        require_missing_worker_deadline("collector_volume_check_completed")

        collector_scrape_after_restart: dict[str, object] = {}
        application_otel_scrape_after_restart: dict[str, object] = {}
        failure_context["worker_heartbeat_missing_scrape_barriers"] = {
            "collector": collector_scrape_after_restart,
            "application_otel": application_otel_scrape_after_restart,
        }
        missing_worker_predicate_observation: dict[str, object] = {}

        def missing_worker_failure_message(reason: str) -> str:
            diagnostic = worker_heartbeat_missing_failure_diagnostics(
                failure_context.get("worker_heartbeat_missing_scrape_barriers"),
                missing_worker_predicate_observation,
            )
            failure_context["worker_heartbeat_missing_failure_observations"] = diagnostic
            return format_worker_heartbeat_missing_failure_message(
                reason,
                diagnostic,
            )

        collector_scrape_deadline = missing_transition_deadline

        def collector_was_scraped_after_restart(request_deadline: float) -> object:
            samples = prometheus_vector(
                prometheus_up_timestamp_query("collector"), request_deadline
            )
            sample_timestamp = prometheus_timestamp_query_sample_after_epoch(
                samples,
                job="collector",
                not_before_epoch=collector_restart_epoch,
            )
            if sample_timestamp is None:
                return None
            collector_scrape_after_restart.update(
                {
                    "collector_target_up": True,
                    "sample_timestamp_seconds": sample_timestamp,
                }
            )
            return collector_scrape_after_restart

        try:
            wait_until_deadline(
                collector_scrape_deadline,
                collector_was_scraped_after_restart,
                failure=(
                    "Prometheus did not scrape the recovered Collector before the worker alert "
                    "phase"
                ),
                interval_seconds=5,
            )
        except AcceptanceFailure:
            record_missing_worker_deadline("collector_scrape_wait_failed")
            raise AcceptanceFailure(
                missing_worker_failure_message(
                    "Prometheus did not scrape the recovered Collector before the worker alert "
                    "phase"
                )
            ) from None
        except Exception:
            record_missing_worker_deadline("collector_scrape_wait_failed")
            raise
        require_missing_worker_deadline("collector_scrape_wait_completed")

        def application_otel_was_scraped_after_restart(request_deadline: float) -> object:
            samples = prometheus_vector(
                prometheus_up_timestamp_query("application-otel"), request_deadline
            )
            sample_timestamp = prometheus_timestamp_query_sample_after_epoch(
                samples,
                job="application-otel",
                not_before_epoch=collector_restart_epoch,
            )
            if sample_timestamp is None:
                return None
            application_otel_scrape_after_restart.update(
                {
                    "application_otel_target_up": True,
                    "sample_timestamp_seconds": sample_timestamp,
                }
            )
            return application_otel_scrape_after_restart

        failure_context["stage"] = "worker_heartbeat_missing_application_otel_scrape"
        try:
            wait_until_deadline(
                missing_transition_deadline,
                application_otel_was_scraped_after_restart,
                failure=(
                    "Prometheus did not scrape the restarted Collector's application metrics "
                    "endpoint before the worker heartbeat check"
                ),
                interval_seconds=5,
            )
        except AcceptanceFailure:
            record_missing_worker_deadline("application_otel_scrape_wait_failed")
            raise AcceptanceFailure(
                missing_worker_failure_message(
                    "Prometheus did not scrape the restarted Collector's application metrics "
                    "endpoint before the worker heartbeat check"
                )
            ) from None
        except Exception:
            record_missing_worker_deadline("application_otel_scrape_wait_failed")
            raise
        record_missing_worker_deadline("application_otel_scrape_wait_completed")
        failure_context["stage"] = "worker_heartbeat_missing_slot_predicate"

        def expected_slot_present_without_current_heartbeat(request_deadline: float) -> object:
            expected_samples = prometheus_vector(
                'prl_expected_worker_slot{worker_slot="1"}', request_deadline
            )
            expected_slot = worker_alert_samples(expected_samples, worker_slot="1")
            current_samples = prometheus_vector(
                "timestamp(prl:worker:heartbeat_current)", request_deadline
            )
            current_heartbeats = worker_alert_samples(
                current_samples,
                worker_slot="1",
            )
            raw_heartbeat_vector = prometheus_vector(
                'prl_worker_heartbeat_timestamp_seconds{worker_slot="1"}',
                request_deadline,
            )
            raw_heartbeats = worker_alert_samples(raw_heartbeat_vector, worker_slot="1")

            def sample_pair(sample: dict[str, object]) -> tuple[float, float] | None:
                value = sample.get("value")
                if not isinstance(value, list) or len(value) != 2:
                    return None
                sample_epoch = _finite_prometheus_number(value[0])
                metric_value = _finite_prometheus_number(value[1])
                if sample_epoch is None or metric_value is None:
                    return None
                return sample_epoch, metric_value

            raw_heartbeat_pairs = [sample_pair(sample) for sample in raw_heartbeats]
            raw_heartbeat_predates_worker_exit = (
                bool(raw_heartbeats)
                and missing_worker_exit_epoch is not None
                and len(raw_heartbeat_pairs) == len(raw_heartbeats)
                and all(
                    pair is not None and pair[1] < missing_worker_exit_epoch
                    for pair in raw_heartbeat_pairs
                )
            )
            current_heartbeat_recorded_after_collector_restart = (
                prometheus_timestamp_query_has_sample_after_epoch(
                    current_heartbeats, not_before_epoch=collector_restart_epoch
                )
            )
            missing_worker_predicate_observation.update(
                {
                    "expected_slot_sample_count": len(expected_slot),
                    "current_heartbeat_sample_count": len(current_heartbeats),
                    "raw_heartbeat_sample_count": len(raw_heartbeats),
                    "raw_heartbeat_predates_worker_exit": (
                        raw_heartbeat_predates_worker_exit
                    ),
                    "current_heartbeat_recorded_after_collector_restart": (
                        current_heartbeat_recorded_after_collector_restart
                    ),
                }
            )
            if len(expected_slot) != 1 or current_heartbeats:
                return None
            raw_value = expected_slot[0].get("value")
            if not isinstance(raw_value, list) or len(raw_value) != 2 or str(raw_value[1]) != "1":
                return None
            return {
                "expected_worker_slot_present": True,
                "expected_slot_value": 1,
                "current_process_heartbeat_absent": True,
                "worker_slot": "1",
            }

        try:
            missing_stimulus_observation = wait_until_deadline(
                missing_transition_deadline,
                expected_slot_present_without_current_heartbeat,
                failure=(
                    "The expected worker slot did not remain present after its current heartbeat "
                    "disappeared"
                ),
                interval_seconds=5,
            )
        except AcceptanceFailure:
            record_missing_worker_deadline("slot_and_heartbeat_predicate_timeout")
            failure_context["worker_heartbeat_missing_predicate"] = dict(
                missing_worker_predicate_observation
            )
            raise AcceptanceFailure(
                missing_worker_failure_message(
                    "The expected worker slot did not remain present after its current heartbeat "
                    "disappeared"
                )
            ) from None
        record_missing_worker_deadline("slot_and_heartbeat_predicate_observed")
        try:
            missing_firing = wait_for_worker_alert_firing(
                "WorkerHeartbeatMissing",
                missing_receiver_baseline,
                missing_transition_deadline,
                stimulus_check=expected_slot_present_without_current_heartbeat,
            )
        except AcceptanceFailure:
            record_missing_worker_deadline("alert_firing_timeout")
            raise AcceptanceFailure(
                missing_worker_failure_message(
                    "WorkerHeartbeatMissing did not fire before the worker restart deadline"
                )
            ) from None
        record_missing_worker_deadline("alert_firing_observed")
        worker_alert_resolution_baselines["WorkerHeartbeatMissing"] = worker_receiver_snapshot(
            "WorkerHeartbeatMissing"
        )
        acceptance_success(
            runtime.compose("up", "-d", "--no-deps", "--no-recreate", "worker", timeout=45),
            "The UUID-owned worker could not restart after WorkerHeartbeatMissing fired",
        )
        missing_worker_started = runtime.inspect_service("worker")
        missing_worker_started_state = _object_mapping(missing_worker_started.get("State"))
        missing_worker_started_labels = _object_mapping(
            _object_mapping(missing_worker_started.get("Config")).get("Labels")
        )
        missing_worker_started_at = missing_worker_started_state.get("StartedAt")
        if (
            missing_worker_started.get("Id") != worker_container_id
            or missing_worker_started_state.get("Status") != "running"
            or missing_worker_started_labels.get("com.docker.compose.project")
            != runtime.stack.project
            or not isinstance(missing_worker_started_at, str)
        ):
            raise AcceptanceFailure(
                "The heartbeat-missing worker restart changed container identity"
            )
        missing_recovery_deadline = MonotonicDeadline.from_utc_timestamp(
            missing_worker_started_at, 90
        )

        def restarted_worker_has_fresh_process_metrics(request_deadline: float) -> object:
            _metric_names, current = runtime.worker_metric_state(deadline=request_deadline)
            started = _worker_metric_value(current, "started")
            heartbeat = _worker_metric_value(current, "heartbeat")
            heartbeat_labels = _object_mapping(
                _object_mapping(current.get("heartbeat")).get("labels")
            )
            container_started_epoch = datetime.fromisoformat(
                missing_worker_started_at.replace("Z", "+00:00")
            ).timestamp()
            if (
                started is None
                or started <= missing_worker_started_before
                or not runtime.worker_started_value_matches_container_generation(
                    started, container_started_epoch
                )
                or heartbeat is None
                or heartbeat < started
                or not 0 <= time.time() - heartbeat <= 45
                or heartbeat_labels.get("worker_slot") != "1"
            ):
                return None
            return {
                "worker_started_timestamp_seconds": started,
                "worker_heartbeat_timestamp_seconds": heartbeat,
                "worker_slot": "1",
                "same_container_identity": True,
                "fresh_process_generation": True,
            }

        missing_recovery_metrics = wait_until_deadline(
            missing_recovery_deadline,
            restarted_worker_has_fresh_process_metrics,
            failure="The restarted worker did not publish fresh process and heartbeat metrics",
            interval_seconds=5,
        )
        missing_resolved = wait_for_worker_alert_resolution(
            "WorkerHeartbeatMissing",
            missing_firing,
            worker_alert_resolution_baselines["WorkerHeartbeatMissing"],
            missing_recovery_deadline,
            lambda request_deadline: bool(
                restarted_worker_has_fresh_process_metrics(request_deadline)
            ),
        )
        worker_alert_evidence["WorkerHeartbeatMissing"] = {
            "stimulus": {
                "worker_exit_at_utc": missing_worker_exit_at_utc,
                "worker_container_id_matches": True,
                "collector_reset_completed": True,
                "collector_scrape_after_reset": collector_scrape_after_restart,
                "worker_slot_remained_present_without_current_heartbeat": (
                    missing_stimulus_observation
                ),
            },
            "firing": missing_firing,
            "recovery_metrics": missing_recovery_metrics,
            "recovery": missing_resolved,
        }

        failure_context["stage"] = "collector_restart_https_readiness"
        after_collector = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if after_collector[0] != 200:
            raise AcceptanceFailure("The app gateway did not recover after the Collector restart")
        failure_context["stage"] = "collector_restart_gateway_event_query"
        initial_gateway_event = evidence.get("gateway_loki_event")
        initial_event_timestamp = (
            initial_gateway_event.get("request_event_timestamp_unix_ns")
            if isinstance(initial_gateway_event, dict)
            else None
        )
        if type(initial_event_timestamp) is not int or initial_event_timestamp <= 0:
            raise AcceptanceFailure("The initial gateway event omitted its Loki entry timestamp")
        recent_query = runtime.observe_loki_request(request_id, trace_id)
        recent_start_ns = recent_query.get("query_start_unix_ns")
        recent_end_ns = recent_query.get("query_end_unix_ns")
        current_time_ns = time.time_ns()
        recent_failure = recent_query.get("failure_category")
        if recent_failure not in {
            None,
            "http_status",
            "api_status",
            "event_cardinality",
            "finite_resource_identity_missing",
            "request_event_missing",
            "trace_correlation_missing",
            "release_missing",
        }:
            recent_failure = "other"
        recent_query_diagnostic = {
            "http_status": recent_query.get("http_status"),
            "body_status_ok": recent_query.get("body_status_ok"),
            "stream_count": recent_query.get("stream_count"),
            "entry_count": recent_query.get("entry_count"),
            "request_id_match": recent_query.get("request_id_match"),
            "trace_id_match": recent_query.get("trace_id_match"),
            "event_match": recent_query.get("event_match"),
            "release_match": recent_query.get("release_match"),
            "failure_category": recent_failure,
            "query_start_unix_ns": recent_start_ns,
            "query_end_unix_ns": recent_end_ns,
        }
        historical_query_start_ns = max(
            1, min(initial_event_timestamp - 1_000_000_000, current_time_ns)
        )
        collector_event_query_diagnostic: dict[str, object] = {
            "initial_event_timestamp_unix_ns": initial_event_timestamp,
            "recent_query_age_seconds": round(
                max(0, current_time_ns - initial_event_timestamp) / 1_000_000_000, 3
            ),
            "recent_query_window_seconds": 600,
            "initial_event_precedes_recent_query_start": (
                type(recent_start_ns) is int and initial_event_timestamp < recent_start_ns
            ),
            "recent_query": recent_query_diagnostic,
            "historical_query_start_unix_ns": historical_query_start_ns,
        }
        failure_context["collector_restart_event_query"] = collector_event_query_diagnostic
        evidence["collector_restart_event_query"] = collector_event_query_diagnostic
        try:
            runtime.last_loki_index_series_diagnostic = {"available": False}
            persisted_gateway_event = runtime.query_loki_request(
                request_id,
                trace_id,
                start_ns=historical_query_start_ns,
            )
        except AcceptanceFailure as exc:
            historical_query = runtime.last_loki_request_diagnostic
            historical_failure = historical_query.get("failure_category")
            if historical_failure not in {
                None,
                "http_status",
                "api_status",
                "event_cardinality",
                "finite_resource_identity_missing",
                "request_event_missing",
                "trace_correlation_missing",
                "release_missing",
            }:
                historical_failure = "other"
            collector_event_query_diagnostic["historical_query"] = {
                "http_status": historical_query.get("http_status"),
                "body_status_ok": historical_query.get("body_status_ok"),
                "stream_count": historical_query.get("stream_count"),
                "entry_count": historical_query.get("entry_count"),
                "request_id_match": historical_query.get("request_id_match"),
                "trace_id_match": historical_query.get("trace_id_match"),
                "event_match": historical_query.get("event_match"),
                "release_match": historical_query.get("release_match"),
                "failure_category": historical_failure,
                "query_start_unix_ns": historical_query.get("query_start_unix_ns"),
                "query_end_unix_ns": historical_query.get("query_end_unix_ns"),
            }
            historical_index = runtime.last_loki_index_series_diagnostic
            historical_index_failure = historical_index.get("failure_category")
            if historical_index_failure not in {
                None,
                "http_status",
                "api_status",
                "invalid_shape",
                "empty_series",
                "index_schema",
            }:
                historical_index_failure = "other"
            collector_event_query_diagnostic["historical_index"] = {
                "http_status": historical_index.get("http_status"),
                "series_count": historical_index.get("series_count"),
                "index_schema_ok": historical_index.get("index_schema_ok"),
                "request_id_indexed": historical_index.get("request_id_indexed"),
                "failure_category": historical_index_failure,
            }
            if str(exc) == "A gateway request did not produce one correlated Loki event":
                collector_event_query_diagnostic["historical_query_failure"] = (
                    "gateway_event_missing_or_invalid"
                )
            elif str(exc) == "Loki gateway series did not retain the finite index schema":
                collector_event_query_diagnostic["historical_query_failure"] = (
                    "gateway_index_schema_invalid"
                )
            else:
                collector_event_query_diagnostic["historical_query_failure"] = (
                    "other_acceptance_failure"
                )
            raise
        collector_event_query_diagnostic["historical_query_matches_initial"] = (
            persisted_gateway_event == initial_gateway_event
        )
        if persisted_gateway_event != initial_gateway_event:
            raise AcceptanceFailure(
                "The persisted gateway event changed or duplicated after Collector restart"
            )
        evidence["persistent_volumes"] = sorted(
            name.removeprefix(runtime.stack.project + "_") for name in runtime.volume_snapshot()
        )
        evidence["worker_alert_observations"] = worker_alert_evidence
        evidence["collector_restart"] = "same file-storage volume; HTTPS and Loki event persisted"

        accepted = True
    finally:
        active_error = sys.exc_info()[1]
        cleanup_error: Exception | None = None
        try:
            runtime.cleanup()
        except Exception as exc:
            cleanup_error = exc

        outcome = "passed" if accepted and cleanup_error is None else "failed"
        if cleanup_error is not None and active_error is None:
            failure_context["stage"] = "cleanup"
        error = active_error or cleanup_error
        try:
            persist_review_evidence(
                outcome,
                type(error).__name__ if error is not None else None,
                failure_context.get("stage", "complete"),
            )
        except Exception:
            print("TASK6_EVIDENCE_WRITE_FAILURE=io_error")
            if error is None:
                raise AcceptanceFailure(
                    "Sanitized observability review evidence could not be persisted"
                ) from None

        if active_error is None and cleanup_error is not None:
            raise cleanup_error


@pytest.mark.skipif(
    not _full_stack_enabled(),
    reason="requires all five opt-in Compose runtime gates",
)
def test_gateway_alert_resolution_runtime_diagnostic(tmp_path: Path) -> None:
    """Exercise the pinned Loki-to-Alertmanager-to-receiver transition in isolation."""
    runtime = FullStackRuntime(tmp_path, use_acceptance_token=False)
    evidence: dict[str, object] = {
        "project": runtime.stack.project,
        "source_sha": runtime.stack.expected_sha,
        "started_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "services": ["loki", "alertmanager", "local_alert_receiver"],
    }
    cleanup_succeeded = False
    image_built = False
    image_name = f"prl-backend:{runtime.stack.image_tag}"

    def utc_now() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def query_observation(expression: str) -> dict[str, object]:
        response = runtime.api_json(
            "http://loki:3100/loki/api/v1/query?" + urlencode({"query": expression})
        )
        if response.get("status") != "success":
            raise AcceptanceFailure("The pinned Loki diagnostic query did not succeed")
        samples = response.get("data", {}).get("result", [])
        if not isinstance(samples, list) or len(samples) > 1:
            raise AcceptanceFailure("The pinned Loki diagnostic returned an invalid vector")
        observation: dict[str, object] = {
            "observed_at_utc": utc_now(),
            "sample_timestamp": None,
            "value": None,
            "sample_count": len(samples),
        }
        if samples:
            sample = samples[0]
            try:
                timestamp = str(sample["value"][0])
                value = float(sample["value"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                raise AcceptanceFailure("The Loki diagnostic sample was malformed") from None
            if not math.isfinite(value):
                raise AcceptanceFailure("The Loki diagnostic sample was not finite")
            observation.update(sample_timestamp=timestamp, value=value)
        return observation

    def selected_rule_state() -> dict[str, object]:
        endpoint = "http://loki:3100/prometheus/api/v1/rules?type=alert"
        status = runtime.api_status(endpoint, method="GET", body=None)
        result: dict[str, object] = {"http_status": status, "observed_at_utc": utc_now()}
        if status != 200:
            return result
        payload = runtime.api_json(endpoint)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        groups = data.get("groups", []) if isinstance(data, dict) else []
        for group in groups if isinstance(groups, list) else []:
            for rule in group.get("rules", []) if isinstance(group, dict) else []:
                if (
                    rule.get("name") == "GatewayFastSuccessBelowSLO"
                    or rule.get("alert") == "GatewayFastSuccessBelowSLO"
                ):
                    result["group"] = group.get("name")
                    result["rule"] = {
                        key: rule.get(key)
                        for key in (
                            "name",
                            "alert",
                            "state",
                            "health",
                            "lastEvaluation",
                            "evaluationTime",
                            "lastError",
                            "activeAt",
                        )
                        if key in rule
                    }
                    return result
        result["rule_not_found"] = True
        return result

    def selected_loki_alerts() -> dict[str, object]:
        endpoint = "http://loki:3100/prometheus/api/v1/alerts"
        status = runtime.api_status(endpoint, method="GET", body=None)
        result: dict[str, object] = {"http_status": status, "observed_at_utc": utc_now()}
        if status != 200:
            return result
        payload = runtime.api_json(endpoint)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        alerts = data.get("alerts", []) if isinstance(data, dict) else []
        result["alerts"] = (
            [
                {
                    key: alert.get(key)
                    for key in ("labels", "state", "activeAt", "value", "annotations")
                    if key in alert
                }
                for alert in alerts
                if isinstance(alert, dict)
                and alert.get("labels", {}).get("alertname") == "GatewayFastSuccessBelowSLO"
            ]
            if isinstance(alerts, list)
            else []
        )
        return result

    def selected_alertmanager_alerts() -> list[dict[str, object]]:
        payload = runtime.api_json("http://alertmanager:9093/api/v2/alerts")
        return (
            [
                {
                    key: alert.get(key)
                    for key in ("labels", "status", "startsAt", "endsAt", "receiver", "fingerprint")
                    if key in alert
                }
                for alert in payload
                if isinstance(alert, dict)
                and alert.get("labels", {}).get("alertname") == "GatewayFastSuccessBelowSLO"
            ]
            if isinstance(payload, list)
            else []
        )

    def selected_loki_rule_metrics() -> list[str]:
        script = (
            "import json,sys,urllib.request; "
            "lines=urllib.request.urlopen(sys.argv[1],timeout=5).read().decode().splitlines(); "
            "items=[line for line in lines if not line.startswith('#') "
            "and any(token in line.lower() for token in ('rule','ruler','evaluation')) "
            "and ('gateway-sli-alerts' in line or 'evaluation_failures' in line.lower())]; "
            "print(json.dumps(items[:80],separators=(',',':')))"
        )
        result = acceptance_invoke(
            [
                "docker",
                "exec",
                runtime.api_id,
                "python",
                "-c",
                script,
                "http://loki:3100/metrics",
            ],
            timeout=15,
        )
        if result.returncode:
            return []
        try:
            lines = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        return [line for line in lines if isinstance(line, str)] if isinstance(lines, list) else []

    def selected_receiver_alerts() -> list[dict[str, str]]:
        payload = runtime.api_json("http://local_alert_receiver:8088/alerts")
        alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
        return (
            [
                {key: str(alert[key]) for key in ("alertname", "severity", "status", "received_at")}
                for alert in alerts
                if isinstance(alert, dict)
                and alert.get("alertname") == "GatewayFastSuccessBelowSLO"
            ]
            if isinstance(alerts, list)
            else []
        )

    def boundary_snapshot() -> dict[str, object]:
        return {
            "observed_at_utc": utc_now(),
            "loki_rule": selected_rule_state(),
            "loki_alerts": selected_loki_alerts(),
            "loki_rule_metrics": selected_loki_rule_metrics(),
            "alertmanager_alerts": selected_alertmanager_alerts(),
            "receiver_alerts": selected_receiver_alerts(),
        }

    def wait_receiver_status(status: str, *, timeout: float, interval: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        observations: list[dict[str, object]] = []
        next_heartbeat = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                runtime.push_gateway_json_logs([{"event": "gateway.healthcheck"}])
                next_heartbeat = time.monotonic() + 20
            observed = boundary_snapshot()
            observations.append(observed)
            if any(item.get("status") == status for item in observed["receiver_alerts"]):
                return {"observations": observations, "matched": observed}
            time.sleep(interval)
        raise AcceptanceFailure(
            f"The receiver did not observe GatewayFastSuccessBelowSLO {status} within {timeout}s"
        )

    try:
        acceptance_success(
            runtime.compose("config", "--quiet"), "The isolated Compose files did not parse"
        )
        acceptance_success(
            runtime.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={runtime.stack.expected_sha}",
                "api",
                timeout=900,
            ),
            "The exact-source receiver image could not be built",
        )
        image_built = True
        image_records = acceptance_json(
            acceptance_invoke(["docker", "image", "inspect", image_name]),
            "The isolated receiver image could not be inspected",
        )
        image_labels = (image_records[0].get("Config") or {}).get("Labels") or {}
        if image_labels.get("org.opencontainers.image.revision") != runtime.stack.expected_sha:
            raise AcceptanceFailure("The diagnostic receiver image did not match the source SHA")
        acceptance_success(
            runtime.compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                "loki",
                "alertmanager",
                "local_alert_receiver",
                timeout=180,
            ),
            "The bounded Loki alert path did not start",
        )
        receiver_id = acceptance_success(
            runtime.compose("ps", "-q", "local_alert_receiver"),
            "The isolated receiver container was missing",
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", receiver_id):
            raise AcceptanceFailure("The isolated receiver container identity was invalid")
        receiver_record = acceptance_json(
            acceptance_invoke(["docker", "inspect", receiver_id]),
            "The isolated receiver container could not be inspected",
        )[0]
        receiver_labels = (receiver_record.get("Config") or {}).get("Labels") or {}
        if receiver_labels.get("com.docker.compose.project") != runtime.stack.project:
            raise AcceptanceFailure("The diagnostic receiver failed its exact UUID owner check")
        runtime.api_id = receiver_id
        runtime.stack.wait_for(
            lambda: (
                runtime.api_status("http://loki:3100/ready", method="GET", body=None) == 200
                and runtime.api_status("http://alertmanager:9093/-/ready", method="GET", body=None)
                == 200
                and runtime.api_status(
                    "http://local_alert_receiver:8088/health/ready", method="GET", body=None
                )
                == 200
            ),
            seconds=90,
            failure="The three bounded alert-path services did not become ready",
        )
        evidence["readiness"] = {"loki": 200, "alertmanager": 200, "receiver": 200}
        runtime.push_gateway_json_logs([{"event": "gateway.healthcheck"}])
        evidence["gateway_truth_table"] = runtime.gateway_sli_truth_table()

        rules_document = yaml.safe_load(
            (ROOT / "tools/infra/loki-rules/fake/gateway-sli.yml").read_text(encoding="utf-8")
        )
        recordings = {
            rule["record"]: rule["expr"]
            for group in rules_document["groups"]
            for rule in group["rules"]
            if "record" in rule
        }
        alerts = {
            rule["alert"]: rule
            for group in rules_document["groups"]
            for rule in group["rules"]
            if "alert" in rule
        }
        success_expression = recordings["prl:gateway_successful_requests:sum5m"]
        fast_count_expression = recordings["prl:gateway_fast_successful_requests:sum5m"]
        ratio_expression = recordings["prl:gateway_fast_success:ratio5m"]
        alert_expression = alerts["GatewayFastSuccessBelowSLO"]["expr"]
        initial = {
            "successful_requests": query_observation(success_expression),
            "fast_successful_requests": query_observation(fast_count_expression),
            "fast_success_ratio": query_observation(ratio_expression),
            "exact_alert_predicate": query_observation(alert_expression),
        }
        success_count = initial["successful_requests"]["value"]
        fast_count = initial["fast_successful_requests"]["value"]
        initial_ratio = initial["fast_success_ratio"]["value"]
        exact_predicate = initial["exact_alert_predicate"]["value"]
        if (
            success_count is None
            or success_count < 20
            or fast_count is None
            or initial_ratio is None
            or initial_ratio >= 0.95
            or exact_predicate is None
        ):
            raise AcceptanceFailure("The initial failing gateway SLI fixture was not query-visible")
        evidence["initial_query_vector"] = initial
        evidence["initial_boundary"] = boundary_snapshot()
        evidence["firing_delivery"] = wait_receiver_status("firing", timeout=180, interval=10)
        evidence["firing_boundary"] = boundary_snapshot()

        healthy_needed = math.floor(max(0.0, 0.95 * success_count - fast_count) / 0.05) + 101
        if not 1 <= healthy_needed <= 5000:
            raise AcceptanceFailure("The calculated bounded recovery fixture exceeded its cap")
        recovery_events = [
            {
                "event": "gateway.request",
                "classification": "success",
                "route": "/__task7_resolution_recovery",
                "duration_seconds": 0.2,
            }
            for _ in range(healthy_needed)
        ]
        runtime.push_gateway_json_logs(recovery_events)
        post_push = {
            "successful_requests": query_observation(success_expression),
            "fast_successful_requests": query_observation(fast_count_expression),
            "fast_success_ratio": query_observation(ratio_expression),
            "corrected_alert_predicate": query_observation(alert_expression),
        }
        recovered_success = post_push["successful_requests"]["value"]
        recovered_fast = post_push["fast_successful_requests"]["value"]
        recovered_ratio = post_push["fast_success_ratio"]["value"]
        if (
            recovered_success is None
            or recovered_fast is None
            or recovered_ratio is None
            or recovered_ratio <= 0.95
            or post_push["corrected_alert_predicate"]["value"] is not None
        ):
            evidence["post_push_query_vector"] = post_push
            raise AcceptanceFailure(
                "The healthy recovery fixture did not clear the exact alert predicate"
            )
        old_predicate_expression = f"({ratio_expression}) < 0.95 * ({success_expression} >= 20)"
        old_predicate = query_observation(old_predicate_expression)
        evidence["post_push_query_vector"] = post_push
        evidence["historical_multiplication_shape_diagnostic"] = {
            "label": "regenerated diagnostic expression, not a recovered historical artifact",
            "expression": old_predicate_expression,
            "same_global_cohort_count": recovered_success,
            "same_global_cohort_fast_count": recovered_fast,
            "same_global_cohort_ratio": recovered_ratio,
            "sample": old_predicate,
        }
        if old_predicate["value"] is None:
            raise AcceptanceFailure(
                "The old multiplication diagnostic did not reproduce the false-positive vector"
            )
        evidence["recovery_events_pushed"] = healthy_needed
        evidence["resolved_delivery"] = wait_receiver_status("resolved", timeout=90, interval=5)
        evidence["resolved_boundary"] = boundary_snapshot()
        receiver_records = selected_receiver_alerts()
        firing_records = [item for item in receiver_records if item.get("status") == "firing"]
        resolved_records = [item for item in receiver_records if item.get("status") == "resolved"]
        if not firing_records or not resolved_records:
            raise AcceptanceFailure("The bounded receiver did not retain both transition summaries")
        evidence["receiver_transition"] = {
            "firing": firing_records[-1],
            "resolved": resolved_records[-1],
        }
    finally:
        try:
            runtime.cleanup()
            volumes_before_remove = runtime.volume_snapshot()
            if any(
                value["labels"].get("com.docker.compose.project") != runtime.stack.project
                for value in volumes_before_remove.values()
            ):
                raise AcceptanceFailure("A diagnostic volume failed its exact UUID owner check")
            acceptance_success(
                runtime.compose(
                    "down", "--volumes", "--remove-orphans", "--timeout", "30", timeout=60
                ),
                "The isolated UUID-owned Compose volumes could not be removed",
            )
            if runtime.volume_snapshot():
                raise AcceptanceFailure("The diagnostic left a UUID-owned Compose volume behind")
            runtime.stack.assert_demo_untouched()
            evidence["cleanup"] = {
                "project": runtime.stack.project,
                "uuid_owned_volumes_removed": len(volumes_before_remove),
                "prl_demo_untouched": True,
            }
            if image_built:
                acceptance_success(
                    acceptance_invoke(["docker", "image", "rm", image_name]),
                    "The exact diagnostic receiver image tag could not be removed",
                )
            cleanup_succeeded = True
        except Exception as cleanup_error:
            evidence["cleanup_error_category"] = type(cleanup_error).__name__
            raise
        finally:
            evidence["cleanup_succeeded"] = cleanup_succeeded
            print(
                "TASK7_GATEWAY_ALERT_TRANSITION="
                + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
            )
