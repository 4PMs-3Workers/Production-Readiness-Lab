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
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import urlencode, urlparse
from uuid import UUID, uuid4

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
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

ROOT = Path(__file__).resolve().parents[2]
NGINX_IMAGE = (
    "nginx:stable-alpine" "@sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576"
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
) -> None:
    """Restart the existing worker container while MySQL is stopped."""
    acceptance_success(
        compose("stop", "--timeout", "10", "worker", timeout=30),
        "The UUID-owned worker could not stop for the same-slot observation",
    )
    acceptance_success(
        compose("stop", "--timeout", "10", "mysql", timeout=30),
        "The UUID-owned database could not stop for the same-slot observation",
    )
    acceptance_success(
        compose("start", "worker", timeout=45),
        "The stopped worker could not start while MySQL was unavailable",
    )


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
    rows: list[tuple[dict[str, object], dict[str, object]]] = []
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
            try:
                message = json.loads(entry[1]) if isinstance(entry[1], str) else {}
            except (json.JSONDecodeError, TypeError):
                message = {}
            fields = message if isinstance(message, dict) else {}
            metadata = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            combined = {**fields, **metadata}
            field_keys.update(str(key) for key in combined)
            rows.append((labels, combined))

    request_match = any(fields.get("request_id") == request_id for _, fields in rows)
    trace_match = any(fields.get("trace_id") == trace_id for _, fields in rows)
    event_match = any(fields.get("event") == "gateway.request" for _, fields in rows)
    release_match = any(fields.get("release") == expected_release for _, fields in rows)
    trace_format = any(
        re.fullmatch(r"[a-f0-9]{32}", str(fields.get("trace_id", ""))) for _, fields in rows
    )
    finite_resource_identity_ok = any(
        labels.get("service_name") == "gateway"
        and labels.get("deployment_environment_name") == "demo"
        for labels, _ in rows
    )
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
            assert not GATEWAY_SUBNET.overlaps(
                ipaddress.ip_network(value)
            ), "the fixed gateway fixture subnet overlaps an existing Docker network"


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
            "PRL_OPS_CIDRS=192.0.2.5/32",
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
    owner = f"{OWNER_LABEL}={token}"
    cert, key = make_certificate(tmp_path)
    entrypoint = ROOT / "tools/deploy/nginx/gateway-entrypoint.sh"
    original = entrypoint.read_text(encoding="utf-8")
    dynamic_lines = (
        "        set $prl_ops_upstream http://grafana:3000;\n"
        "        proxy_pass $prl_ops_upstream;"
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
        for name in (old_gateway_name, gateway_name, api_name):
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

    def compose(self, *args: str, timeout: float = 180):
        return self.stack.compose(*args, timeout=timeout)

    def api_json(self, url: str, *, method: str = "GET", body: object | None = None):
        script = (
            "import json,sys,urllib.request; "
            "url,method,raw=sys.argv[1:4]; "
            "data=raw.encode() if raw else None; "
            "req=urllib.request.Request(url,data=data,method=method,"
            "headers={'Content-Type':'application/json'} if data else {}); "
            "res=urllib.request.urlopen(req,timeout=5); "
            "print(json.dumps(json.loads(res.read()),separators=(',',':')))"
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
                json.dumps(body, separators=(",", ":")) if body is not None else "",
            ],
            timeout=15,
        )
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

    def alert_receiver_statuses(self, name: str) -> list[dict[str, str]]:
        records = self.api_json("http://local_alert_receiver:8088/alerts").get("alerts", [])
        matching = [
            {"status": item["status"], "severity": item["severity"]}
            for item in records
            if isinstance(item, dict) and item.get("alertname") == name
        ]
        if any(
            item["status"] not in {"firing", "resolved"}
            or item["severity"] not in {"critical", "warning", "info"}
            for item in matching
        ):
            raise AcceptanceFailure("The local receiver returned an invalid rule alert summary")
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

    def observe_loki_request(self, request_id: str, trace_id: str) -> dict[str, object]:
        query = (
            '{service_name="gateway",deployment_environment_name="demo"} '
            f'| request_id="{request_id}"'
        )
        now_ns = time.time_ns()
        params = urlencode(
            {
                "query": query,
                "start": str(now_ns - 600_000_000_000),
                "end": str(now_ns + 60_000_000_000),
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
        summary["transport_error_category"] = observation.get("error_category")
        summary["json_valid"] = observation.get("json_valid")
        summary["body_truncated"] = observation.get("truncated")
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

    def volume_snapshot(self) -> dict[str, dict[str, object]]:
        listed = acceptance_success(
            acceptance_invoke(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.stack.project}",
                ]
            ),
            "The UUID-owned Compose volumes could not be listed",
        )
        names = [name for name in listed.splitlines() if name]
        records = (
            acceptance_json(
                acceptance_invoke(["docker", "volume", "inspect", *names]),
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

    def inspect_service(self, service: str) -> dict[str, object]:
        identifiers = acceptance_success(
            self.compose("ps", "-a", "-q", service),
            f"The UUID-owned {service} container is missing",
        ).splitlines()
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identifiers[0]):
            raise AcceptanceFailure(f"The UUID-owned {service} container identity is invalid")
        identifier = identifiers[0]
        records = acceptance_json(
            acceptance_invoke(["docker", "inspect", identifier]),
            f"The UUID-owned {service} container could not be inspected",
        )
        if len(records) != 1 or (
            records[0].get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
            != self.stack.project
        ):
            raise AcceptanceFailure(f"The {service} container failed its exact owner check")
        return records[0]

    def wait_targets(self) -> list[dict[str, str]]:
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
            response = self.api_json("http://prometheus:9090/api/v1/targets?state=active")
            observed = response.get("data", {}).get("activeTargets", [])
            up = {
                target.get("labels", {}).get("job")
                for target in observed
                if target.get("health") == "up"
            }
            return up == expected and len(observed) == len(expected)

        self.stack.wait_for(
            ready, seconds=90, failure="The complete Prometheus target matrix did not become UP"
        )
        return [
            {
                "job": target["labels"]["job"],
                "health": target["health"],
                "instance": target["labels"]["instance"],
            }
            for target in sorted(observed, key=lambda item: item["labels"]["job"])
        ]

    def worker_metric_state(self):
        response = self.api_json("http://prometheus:9090/api/v1/label/__name__/values")
        names = sorted(
            name
            for name in response.get("data", [])
            if isinstance(name, str) and name.startswith("prl_worker_")
        )
        roles = {
            "started": "started_timestamp_seconds",
            "heartbeat": "heartbeat_timestamp_seconds",
            "last_successful_scan": "last_successful_scan_timestamp_seconds",
            "last_progress": "last_progress_timestamp_seconds",
            "backlog_count": "backlog_count",
            "successful_scans": "successful_scans_total",
        }
        selected = {
            role: next((name for name in names if fragment in name), "")
            for role, fragment in roles.items()
        }
        selected = {role: name for role, name in selected.items() if name}
        state = {}
        for role, metric_name in selected.items():
            response = self.api_json(
                "http://prometheus:9090/api/v1/query?" + urlencode({"query": metric_name})
            )
            samples = response.get("data", {}).get("result", [])
            candidates = [
                sample
                for sample in samples
                if sample.get("metric", {}).get("service_name") == "worker"
                and sample.get("metric", {}).get("deployment_environment_name") == "demo"
                and sample.get("metric", {}).get("worker_slot") == "1"
            ]
            if candidates:
                state[role] = {
                    "name": metric_name,
                    "labels": candidates[0]["metric"],
                    "value": float(candidates[0]["value"][1]),
                }
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

        def timestamp(state: dict[str, dict[str, object]], role: str) -> float | None:
            sample = state.get(role)
            if not isinstance(sample, dict):
                return None
            value = sample.get("value")
            labels = sample.get("labels")
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value < 10_000_000_000
                or not isinstance(labels, dict)
                or any(labels.get(name) != expected for name, expected in identity.items())
            ):
                return None
            return float(value)

        previous_started = timestamp(previous_state, "started")
        started = timestamp(current_state, "started")
        previous_scan = timestamp(previous_state, "last_successful_scan")
        scan = timestamp(current_state, "last_successful_scan")
        previous_progress = timestamp(previous_state, "last_progress")
        progress = timestamp(current_state, "last_progress")
        return {
            "previous_started_timestamp_seconds": (
                round(previous_started, 3) if previous_started is not None else None
            ),
            "started_timestamp_seconds": round(started, 3) if started is not None else None,
            "started_advanced": (
                previous_started is not None and started is not None and started > previous_started
            ),
            "previous_last_successful_scan_timestamp_seconds": (
                round(previous_scan, 3) if previous_scan is not None else None
            ),
            "last_successful_scan_timestamp_seconds": (
                round(scan, 3) if scan is not None else None
            ),
            "last_successful_scan_not_older": (
                previous_scan is not None and scan is not None and scan >= previous_scan
            ),
            "last_successful_scan_precedes_start": (
                started is not None and scan is not None and scan < started
            ),
            "previous_last_progress_timestamp_seconds": (
                round(previous_progress, 3) if previous_progress is not None else None
            ),
            "last_progress_timestamp_seconds": (
                round(progress, 3) if progress is not None else None
            ),
            "last_progress_not_older": (
                previous_progress is not None
                and progress is not None
                and progress >= previous_progress
            ),
            "last_progress_precedes_start": (
                started is not None and progress is not None and progress < started
            ),
        }

    @staticmethod
    def worker_restart_retained_prior_points(summary: dict[str, object]) -> bool:
        return (
            isinstance(summary, dict)
            and summary.get("started_advanced") is True
            and summary.get("last_successful_scan_not_older") is True
            and summary.get("last_successful_scan_precedes_start") is True
            and summary.get("last_progress_not_older") is True
            and summary.get("last_progress_precedes_start") is True
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

    def query_loki_request(self, request_id: str, trace_id: str) -> dict[str, object]:
        query = self.observe_loki_request(request_id, trace_id)
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

    def safe_service_snapshot() -> (
        tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]
    ):
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
    token = os.environ.get("PRL_TASK6_ACCEPTANCE_TOKEN", "")
    if token:
        print(f"Task 6 runtime owner: prl-task6-{token}")
    runtime = FullStackRuntime(tmp_path, pin_ops_edge_network=True)
    evidence: dict[str, object] = {}
    sink_observations: dict[str, dict[str, object]] = {}
    failure_context: dict[str, object] = {
        "stage": "runtime_start",
        "route_status": None,
        "request_id": "",
        "trace_id": "",
        "worker_restart_observations": {"available": False},
    }
    accepted = False
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
        evidence["gateway_sli_truth_table"] = runtime.gateway_sli_truth_table()
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
        evidence["dashboard_screenshots"] = capture_dashboard_screenshots(runtime)

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

        def gateway_rule_firing_received() -> bool:
            return any(
                item["status"] == "firing" and item["severity"] == "warning"
                for item in runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
            )

        runtime.stack.wait_for(
            gateway_rule_firing_received,
            seconds=180,
            failure="The pinned Loki ruler did not deliver the real gateway SLI alert",
        )
        evidence["gateway_rule_firing"] = {
            "alertname": "GatewayFastSuccessBelowSLO",
            "status": "firing",
        }

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
        success_count = runtime.loki_scalar(success_expression)
        fast_count = runtime.loki_scalar(fast_count_expression)
        current_ratio = runtime.loki_scalar(ratio_expression)
        current_predicate = runtime.loki_scalar(alert_expression)
        evidence["gateway_rule_pre_recovery_query"] = {
            "successful_requests": success_count,
            "fast_successful_requests": fast_count,
            "fast_success_ratio": current_ratio,
            "exact_alert_predicate": current_predicate,
        }
        if (
            success_count is None
            or success_count < 20
            or fast_count is None
            or current_ratio is None
            or current_ratio >= 0.95
            or current_predicate is None
        ):
            raise AcceptanceFailure("The live gateway SLI did not meet the firing truth table")
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
        runtime.push_gateway_json_logs(recovery_events)
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

        def gateway_rule_resolved_received() -> bool:
            return any(
                item["status"] == "resolved" and item["severity"] == "warning"
                for item in runtime.alert_receiver_statuses("GatewayFastSuccessBelowSLO")
            )

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
        }
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
        _pre_restart_names, pre_restart_state = runtime.worker_metric_state()
        pre_restart_summary = runtime.worker_restart_observation_summary(
            pre_restart_state, pre_restart_state
        )
        previous_fields = (
            "previous_started_timestamp_seconds",
            "previous_last_successful_scan_timestamp_seconds",
            "previous_last_progress_timestamp_seconds",
        )
        worker_restart_context: dict[str, object] = {
            "pre_restart": {name: pre_restart_summary.get(name) for name in previous_fields}
        }
        failure_context["worker_restart_observations"] = worker_restart_context
        if any(pre_restart_summary.get(name) is None for name in previous_fields):
            raise AcceptanceFailure(
                "The current worker omitted finite same-slot baseline timestamps"
            )
        old_started = _worker_metric_value(pre_restart_state, "started")
        if old_started is None:
            raise AcceptanceFailure("The current worker omitted its numeric start timestamp")
        old_owner_progress = runtime.stack.wait_heartbeat()
        recovery_fixture = business_probe.prepare_due_expiry_fixture()
        worker_restart_context["due_expiry_fixture_ready"] = True
        failure_context["stage"] = "worker_and_database_restart"
        restart_worker_while_database_is_unavailable(runtime.compose)
        new_owner_container = runtime.inspect_service("worker")
        new_owner_config = _object_mapping(new_owner_container.get("Config"))
        new_owner_labels = _object_mapping(new_owner_config.get("Labels"))
        new_owner_state = _object_mapping(new_owner_container.get("State"))
        if (
            new_owner_container.get("Id") != recovery_fixture["worker_container_id"]
            or new_owner_state.get("Status") != "running"
            or new_owner_labels.get("com.docker.compose.project") != runtime.stack.project
        ):
            raise AcceptanceFailure(
                "The new worker owner changed container identity or UUID ownership"
            )
        new_state: dict[str, dict[str, object]] = {}

        def observed_restart() -> bool:
            nonlocal new_state
            _names, new_state = runtime.worker_metric_state()
            started_at = _worker_metric_value(new_state, "started")
            return (
                started_at is not None
                and started_at > old_started
                and "last_successful_scan" in new_state
                and "last_progress" in new_state
                and "successful_scans" in new_state
            )

        runtime.stack.wait_for(
            observed_restart,
            seconds=45,
            failure="The new worker start and cached same-slot points were not observed",
        )
        restart_summary = runtime.worker_restart_observation_summary(pre_restart_state, new_state)
        failure_context["worker_restart_observations"] = restart_summary
        if not runtime.worker_restart_retained_prior_points(restart_summary):
            raise AcceptanceFailure(
                "Collector did not retain finite prior same-slot worker timestamps across restart"
            )
        new_owner_started = _worker_metric_value(new_state, "started")
        if new_owner_started is None:
            raise AcceptanceFailure("The new worker start timestamp was not numeric")
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
            minimum_scan_timestamp=new_owner_started,
        )
        new_owner_worker_id = recovery_scan_before.worker_id
        recovered_owner_state: dict[str, dict[str, object]] = {}

        def new_owner_scan_and_progress_after_database_recovery() -> bool:
            nonlocal recovered_owner_state
            _names, recovered_owner_state = runtime.worker_metric_state()
            started_at = _worker_metric_value(recovered_owner_state, "started")
            successful_scan_at = _worker_metric_value(recovered_owner_state, "last_successful_scan")
            progress_at = _worker_metric_value(recovered_owner_state, "last_progress")
            return (
                started_at == new_owner_started
                and successful_scan_at is not None
                and successful_scan_at > new_owner_started
                and progress_at is not None
                and progress_at > new_owner_started
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
        recovery_appointment_id = recovery_fixture.get("appointment_id")
        recovery_proposal_id = recovery_fixture.get("proposal_id")
        if not isinstance(recovery_appointment_id, str) or not isinstance(
            recovery_proposal_id, str
        ):
            raise AcceptanceFailure("The worker recovery fixture omitted its business identifiers")
        runtime.stack.wait_for(
            lambda: runtime.stack.sql(
                "SELECT CONCAT(cp.status, ':', a.status, ':', COALESCE(a.cancel_reason, '-')) "
                "FROM change_proposals cp JOIN appointments a ON a.id = cp.appointment_id "
                f"WHERE cp.id = UNHEX(REPLACE('{str(UUID(recovery_proposal_id))}', '-', ''));"
            ).strip()
            == "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
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
                minimum_scan_timestamp=new_owner_started,
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
            "scan_and_progress_both_after_new_owner_start": True,
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

        failure_context["stage"] = "collector_restart_and_persistence"
        acceptance_success(
            runtime.compose("restart", "--timeout", "20", "collector", timeout=45),
            "The UUID-owned Collector could not restart",
        )
        runtime.wait_targets()
        if old_volumes != runtime.volume_snapshot():
            raise AcceptanceFailure("Collector restart changed a persistent volume identity")
        after_collector = _https_request(
            runtime.app_host, runtime.port, runtime.app_cert, "GET", "/health/ready"
        )
        if after_collector[0] != 200:
            raise AcceptanceFailure("The app gateway did not recover after the Collector restart")
        if runtime.query_loki_request(request_id, trace_id) != evidence["gateway_loki_event"]:
            raise AcceptanceFailure(
                "The persisted gateway event changed or duplicated after Collector restart"
            )
        evidence["persistent_volumes"] = sorted(
            name.removeprefix(runtime.stack.project + "_") for name in runtime.volume_snapshot()
        )
        evidence["collector_restart"] = "same file-storage volume; HTTPS and Loki event persisted"

        print(
            "TASK6_RUNTIME_EVIDENCE=" + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        )
        accepted = True
    finally:
        if not accepted:
            try:
                route_status_value = failure_context.get("route_status")
                route_status = route_status_value if type(route_status_value) is int else None
                receiver_health_value = failure_context.get("receiver_health")
                receiver_health_summary = (
                    _object_mapping(receiver_health_value)
                    if isinstance(receiver_health_value, dict)
                    else None
                )
                diagnostic = runtime.signal_boundary_diagnostic(
                    route_status=route_status,
                    request_id=str(failure_context["request_id"]),
                    trace_id=str(failure_context["trace_id"]),
                    sink_observations=sink_observations,
                    receiver_health_summary=receiver_health_summary,
                )
                diagnostic["failure_stage"] = failure_context["stage"]
                diagnostic["collector_outage_readiness"] = failure_context.get(
                    "collector_outage_readiness", {"available": False}
                )
                diagnostic["worker_restart_observations"] = failure_context.get(
                    "worker_restart_observations", {"available": False}
                )
                print(
                    "TASK6_PRE_CLEANUP_FAILURE_SNAPSHOT:"
                    + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
                )
            except Exception as exc:
                print(
                    "TASK6_PRE_CLEANUP_FAILURE_SNAPSHOT:"
                    + json.dumps(
                        {
                            "failure_stage": failure_context["stage"],
                            "snapshot_available": False,
                            "diagnostic_error_category": type(exc).__name__,
                            "receiver_health": failure_context.get(
                                "receiver_health", {"available": False}
                            ),
                            "worker_restart_observations": failure_context.get(
                                "worker_restart_observations", {"available": False}
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
        runtime.cleanup()


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
            lambda: runtime.api_status("http://loki:3100/ready", method="GET", body=None) == 200
            and runtime.api_status("http://alertmanager:9093/-/ready", method="GET", body=None)
            == 200
            and runtime.api_status(
                "http://local_alert_receiver:8088/health/ready", method="GET", body=None
            )
            == 200,
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
