"""Strict allowlisted evidence for the PR 42 observability review run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

RUNTIME_SCHEMA = "prl-observability-review-runtime-v1"
FAILURE_SCHEMA = "prl-observability-review-failure-v1"
JOB_TIMEOUT_MINUTES = 45
COLLECTOR_OUTAGE_MINIMUM_SECONDS = 90
GATEWAY_PIPELINE_ALERT_BUDGET_SECONDS = 70

# Mirrors the deployment operator's public, nonsecret configuration identity inputs.
CONFIG_HASH_PATHS = (
    "tools/deploy/compose.yaml",
    "Dockerfile.backend",
    "Dockerfile.frontend",
    "tools/deploy/mysql/10-identities.sh",
    "tools/deploy/mysql/apply-grants.sh",
    "tools/deploy/mysql/grants.sql",
    "tools/deploy/compose.https.yaml",
    "tools/deploy/nginx/nginx.conf",
    "tools/deploy/nginx/https.conf",
    "tools/deploy/nginx/gateway-entrypoint.sh",
    "tools/deploy/nginx/proxy.conf",
    "tools/deploy/nginx/routes.conf",
    "tools/deploy/nginx/route-map.conf",
    "tools/deploy/nginx/file-sink.conf",
    "tools/deploy/compose.observability.yaml",
    "tools/deploy/nginx/file-sink-observability.conf",
    "tools/infra/images.lock.json",
    "tools/infra/collector.Dockerfile",
    "tools/infra/otel-collector.yaml",
    "tools/infra/prometheus.yml",
    "tools/infra/prometheus-rules.yml",
    "tools/infra/loki.yaml",
    "tools/infra/tempo.yaml",
    "tools/infra/alertmanager.yml",
    "tools/infra/grafana-datasources.yml",
    "tools/infra/grafana-dashboard-providers.yml",
    "tools/infra/grafana/dashboards/gateway.json",
    "tools/infra/grafana/dashboards/api-errors.json",
    "tools/infra/grafana/dashboards/db-pool.json",
    "tools/infra/grafana/dashboards/worker-progress.json",
    "tools/infra/grafana/dashboards/telemetry-loss.json",
    "tools/infra/loki-rules/fake/gateway-sli.yml",
    "tools/infra/prometheus-rule-tests.yml",
    "tools/infra/gateway-log-rotate.sh",
    "src/backend/app/observability/alert_receiver.py",
)

_WORKER_ALERTS = {
    "WorkerHeartbeatStale": ("critical", 90, 90),
    "WorkerHeartbeatMissing": ("critical", 90, 90),
    "WorkerProgressStale": ("critical", 180, 120),
    "WorkerBacklogSnapshotStale": ("warning", 180, 120),
    "WorkerBacklogOverdue": ("warning", 220, 120),
}
_BUSINESS_CASES = (
    "healthy",
    "collector_stopped",
    "loki_stopped",
    "tempo_stopped",
    "prometheus_stopped",
)
_ROUTE_TEMPLATES = {"/api/v1/appointments"}
_DASHBOARDS = {
    "prl-gateway": "Gateway SLI",
    "prl-api-errors": "API errors",
    "prl-db-pool": "DB / pool",
    "prl-worker-progress": "Worker progress / backlog",
    "prl-telemetry-loss": "Telemetry loss",
}
_ERROR_CATEGORIES = {
    "AcceptanceFailure": "acceptance_failure",
    "TimeoutExpired": "timeout",
    "TimeoutError": "timeout",
    "OSError": "io_error",
    "CalledProcessError": "subprocess_failure",
    "JSONDecodeError": "invalid_json",
}


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _digest(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def sanitize_alert_fingerprint(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16,64}", value) else None


def _number(
    value: object, *, minimum: float = 0, maximum: float = 1_000_000_000
) -> int | float | None:
    if type(value) not in (int, float):
        return None
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        return None
    return int(value) if type(value) is int else round(result, 3)


def _count(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        return None
    return value


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _bool(mapping: Mapping[str, object], key: str) -> bool:
    return mapping.get(key) is True


def collect_config_hashes(root: Path) -> dict[str, str | None]:
    """Hash only the fixed nonsecret deployment/configuration input allowlist."""
    result: dict[str, str | None] = {}
    for relative in CONFIG_HASH_PATHS:
        try:
            result[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            result[relative] = None
    return result


def _project_config_hashes(value: object) -> dict[str, str | None]:
    raw = _mapping(value)
    return {path: _digest(raw.get(path)) for path in CONFIG_HASH_PATHS}


def _project_delivery(value: object, *, expected_status: str, severity: str) -> dict[str, object]:
    delivery = _mapping(value)
    fingerprint = sanitize_alert_fingerprint(delivery.get("fingerprint"))
    starts_at = _timestamp(delivery.get("starts_at"))
    received_at = _timestamp(delivery.get("received_at"))
    observed = (
        delivery.get("status") == expected_status
        and delivery.get("severity") == severity
        and fingerprint is not None
        and starts_at is not None
        and received_at is not None
    )
    return {
        "observed": observed,
        "received_at_utc": received_at if observed else None,
        "fingerprint": fingerprint if observed else None,
        "starts_at_utc": starts_at if observed else None,
    }


def _project_worker_alerts(value: object) -> dict[str, dict[str, object]]:
    raw = _mapping(value)
    result: dict[str, dict[str, object]] = {}
    for name, (severity, firing_budget, recovery_budget) in _WORKER_ALERTS.items():
        episode = _mapping(raw.get(name))
        firing = _mapping(episode.get("firing"))
        recovery = _mapping(episode.get("recovery"))
        firing_delivery = _project_delivery(
            firing.get("receiver_delivery"), expected_status="firing", severity=severity
        )
        recovery_delivery = _project_delivery(
            recovery.get("receiver_delivery"), expected_status="resolved", severity=severity
        )
        firing_identity_matches = (
            firing_delivery["observed"]
            and sanitize_alert_fingerprint(firing.get("fingerprint"))
            == firing_delivery["fingerprint"]
            and _timestamp(firing.get("starts_at")) == firing_delivery["starts_at_utc"]
        )
        recovery_identity_matches = (
            recovery_delivery["observed"]
            and sanitize_alert_fingerprint(recovery.get("fingerprint"))
            == firing_delivery["fingerprint"]
            and _timestamp(recovery.get("starts_at")) == firing_delivery["starts_at_utc"]
        )
        firing_observed = firing_identity_matches is True
        recovery_observed = firing_observed and recovery_identity_matches is True
        result[name] = {
            "alertname": name,
            "severity": severity,
            "worker_slot": None if name == "WorkerBacklogOverdue" else "1",
            "status": "resolved"
            if recovery_observed
            else "firing"
            if firing_observed
            else "not_observed",
            "fingerprint": firing_delivery["fingerprint"] if firing_observed else None,
            "starts_at_utc": firing_delivery["starts_at_utc"] if firing_observed else None,
            "firing_budget_seconds": firing_budget,
            "detection_elapsed_seconds": _number(firing.get("detection_elapsed_seconds")),
            "firing_poll_count": _count(firing.get("poll_count")),
            "firing_receiver_received_at_utc": (
                firing_delivery["received_at_utc"] if firing_observed else None
            ),
            "recovery_budget_seconds": recovery_budget,
            "recovery_elapsed_seconds": _number(recovery.get("recovery_elapsed_seconds")),
            "recovery_poll_count": _count(recovery.get("poll_count")),
            "recovery_receiver_received_at_utc": (
                recovery_delivery["received_at_utc"] if recovery_observed else None
            ),
            "prometheus_predicate_cleared": _bool(recovery, "prometheus_predicate_cleared"),
            "prometheus_firing_cleared": _bool(recovery, "prometheus_firing_cleared"),
            "real_recovery_observed": _bool(recovery, "real_recovery_observed"),
        }
    return result


def _project_route_attribution(evidence: Mapping[str, object]) -> dict[str, object]:
    correlation = _mapping(evidence.get("business_trace_correlation"))
    gateway = _mapping(evidence.get("gateway_loki_event"))
    route = correlation.get("api_log_route")
    return {
        "api_route": route if isinstance(route, str) and route in _ROUTE_TEMPLATES else None,
        "gateway_event": "gateway.request" if gateway.get("event") == "gateway.request" else None,
        "gateway_request_log_matched": (
            _bool(correlation, "gateway_request_log_matches") and _bool(gateway, "request_id_match")
        ),
        "api_request_log_matched": _bool(correlation, "api_request_log_matches"),
        "tempo_trace_stored": _bool(correlation, "tempo_trace_stored"),
        "mysql_query_span_present": _bool(correlation, "mysql_db_query_span_present"),
        "sql_statement_attributes_absent": _bool(
            correlation, "sql_statement_and_parameter_attributes_absent"
        ),
        "matched_gateway_series_count": _count(gateway.get("index_series_count")),
    }


def _project_gateway_sli(evidence: Mapping[str, object]) -> dict[str, object]:
    table = _mapping(evidence.get("gateway_sli_truth_table"))
    return {
        "alertname": "GatewayFastSuccessBelowSLO",
        "availability_success_over_eligible": _number(
            table.get("availability_success_over_eligible"), maximum=1
        ),
        "fast_success_inclusive_500ms": _number(
            table.get("fast_success_inclusive_500ms"), maximum=1
        ),
        "all_errors_availability_zero": _number(
            table.get("all_errors_availability_zero"), maximum=1
        ),
        "all_errors_fast_success_unknown": table.get("all_errors_fast_success_has_data") is False,
        "all_errors_alert_suppressed": table.get("all_errors_alert_predicate") is None,
        "empty_window_is_unknown": table.get("empty_eligible_window_has_data") is False,
        "excluded_only_is_unknown": table.get("excluded_only_has_data") is False,
        "genuine_healthchecks_in_one_minute": _count(
            table.get("genuine_gateway_healthchecks_in_1m")
        ),
        "fast_success_alert_predicate_value": _number(
            table.get("fast_success_alert_predicate_value"), maximum=1
        ),
        "all_slow_successful_requests": _count(table.get("all_slow_successful_requests")),
        "all_slow_alert_predicate_value": _number(table.get("all_slow_alert_predicate"), maximum=1),
        "all_slow_alert_present": _bool(table, "all_slow_alert_present"),
        "subminimum_alert_suppressed": _bool(table, "sub20_alert_suppressed"),
        "recovered_alert_suppressed": _bool(table, "recovered_alert_suppressed"),
        "fixture_event_count": _count(table.get("pushed_gateway_json_events")),
    }


def _received_status(value: object, status: str) -> bool:
    rows = value if isinstance(value, list) else []
    return any(isinstance(row, Mapping) and row.get("status") == status for row in rows)


def _project_collector_outage(evidence: Mapping[str, object]) -> dict[str, object]:
    outage = _mapping(evidence.get("collector_outage_isolation"))
    expiry = _mapping(evidence.get("gateway_sli_samples_after_heartbeat_expiry"))
    recovered = _mapping(evidence.get("gateway_sli_samples_after_collector_recovery"))
    healthchecks = _mapping(evidence.get("genuine_gateway_healthchecks_after_collector_recovery"))
    delta = _mapping(_mapping(evidence.get("collector_outage_cumulative_loss")).get("delta"))
    missing_firing = _mapping(evidence.get("collector_pipeline_missing_firing"))
    missing_resolved = _mapping(evidence.get("collector_pipeline_missing_resolved"))
    warning_firing = _mapping(evidence.get("collector_target_warning_firing"))
    warning_resolved = _mapping(evidence.get("collector_target_warning_resolved"))
    denominators = _mapping(evidence.get("gateway_request_denominators_at_heartbeat_expiry"))
    return {
        "pipeline_missing_alertname": "GatewayPipelineHeartbeatMissing",
        "target_down_warning_alertname": "TelemetryTargetDown",
        "minimum_outage_seconds": COLLECTOR_OUTAGE_MINIMUM_SECONDS,
        "pipeline_missing_firing_budget_seconds": GATEWAY_PIPELINE_ALERT_BUDGET_SECONDS,
        "collector_stopped_and_preserved": (
            outage.get("collector_stopped_status") == "exited"
            and _bool(outage, "collector_container_preserved")
        ),
        "gateway_ready_while_collector_stopped": _bool(
            outage, "gateway_ready_while_collector_stopped"
        ),
        "collector_recovered": _bool(outage, "collector_recovered"),
        "outage_elapsed_seconds": _number(outage.get("collector_outage_elapsed_seconds")),
        "denominators_positive_at_heartbeat_expiry": bool(denominators)
        and all(_number(value) is not None and float(value) > 0 for value in denominators.values()),
        "gateway_sli_absent_after_heartbeat_expiry": (
            _bool(expiry, "ratio_samples_absent")
            and _bool(expiry, "fast_success_alert_predicate_absent")
        ),
        "gateway_sli_restored_after_recovery": _bool(recovered, "ratio_samples_returned"),
        "post_recovery_healthcheck_count": _count(healthchecks.get("observed_events")),
        "pipeline_missing_firing_received": _received_status(
            _mapping(missing_firing).get("receiver_statuses"), "firing"
        ),
        "pipeline_missing_resolved_received": _received_status(
            _mapping(missing_resolved).get("receiver_statuses"), "resolved"
        ),
        "target_down_warning_firing_observed": _number(
            warning_firing.get("receiver_first_observed_unix_ns"), maximum=10**20
        )
        is not None,
        "target_down_warning_resolved_observed": _number(
            warning_resolved.get("receiver_first_observed_unix_ns"), maximum=10**20
        )
        is not None,
        "failed_export_delta": _number(delta.get("failed")),
        "dropped_export_delta": _number(delta.get("dropped")),
    }


def _project_effect_counts(value: object, *, prefix: str) -> dict[str, int | None]:
    effects = _mapping(value)
    return {
        f"{prefix}_audit_event_count": _count(effects.get("audit_event_count")),
        f"{prefix}_notification_count": _count(effects.get("notification_count")),
    }


def _project_business_recovery(
    evidence: Mapping[str, object], worker_alerts: Mapping[str, object]
) -> dict[str, object]:
    matrix = _mapping(evidence.get("business_outage_matrix"))
    cases: dict[str, object] = {}
    for state in _BUSINESS_CASES:
        case = _mapping(matrix.get(state))
        if not case:
            continue
        expiry = _mapping(case.get("actual_worker_expiry"))
        before = _count(expiry.get("followup_scan_counter_before"))
        after = _count(expiry.get("followup_scan_counter_after"))
        cases[state] = {
            "application_ready": case.get("application_readiness_http") == 200,
            "gateway_ready": case.get("gateway_readiness_http") == 200,
            "authenticated_sessions": _bool(case, "authenticated_student_and_staff_sessions"),
            "booking_replay_stable": _bool(case, "booking_replay_receipt_stable"),
            "proposal_replay_stable": _bool(case, "proposal_replay_receipt_stable"),
            "proposal_accept_replay_stable": _bool(case, "proposal_accept_replay_stable"),
            "cancellation_succeeded": case.get("successful_cancellation_route") == 200,
            **_project_effect_counts(case.get("accepted_business_effects"), prefix="accepted"),
            **_project_effect_counts(case.get("expired_business_effects"), prefix="expired"),
            "proposal_expired_by_worker": (
                expiry.get("actual_worker_transition")
                == "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED"
            ),
            "followup_scan_advanced": before is not None and after is not None and after > before,
            "expiry_effects_stable_after_followup_scan": _bool(
                expiry, "expiry_side_effects_stable_after_followup_scan"
            ),
            "write_latency_max_ms": _number(case.get("write_latency_max_ms"), maximum=60_000),
            "write_latency_count": _count(case.get("write_latency_count")),
        }

    new_owner = _mapping(evidence.get("new_owner_database_recovery"))
    overdue = _mapping(
        _mapping(worker_alerts.get("WorkerBacklogOverdue")).get("business_invariants")
    )
    return {
        "outage_cases": cases,
        "new_owner_database_recovery": {
            "worker_process_changed": _bool(new_owner, "worker_process_changed_from_pre_restart"),
            "series_identity_retained": _bool(
                new_owner, "series_identity_matches_stopped_baseline"
            ),
            "scan_advanced": _bool(new_owner, "scan_advanced_since_stopped_db_baseline"),
            "expiry_followup_scan_before": _count(
                new_owner.get("scan_counter_before_expiry_followup")
            ),
            "expiry_followup_scan_after": _count(new_owner.get("later_scan_counter_after_expiry")),
        },
        "overdue_alert_expiry": {
            "transition_correct": overdue.get("audit_expired_cancelled_reason")
            == "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
            "expiry_audit_count": _count(overdue.get("proposal_expired_audits")),
            "expiry_notification_count": _count(overdue.get("proposal_expired_notifications")),
            "later_scan_duplicated_effects": overdue.get("later_scan_duplicated_effects") is False,
        },
    }


def _project_screenshots(value: object) -> dict[str, object]:
    screenshots_value = _mapping(value).get("screenshots")
    rows = screenshots_value if isinstance(screenshots_value, list) else []
    screenshots: list[dict[str, object]] = []
    seen: set[str] = set()
    for row_value in rows:
        row = _mapping(row_value)
        uid = row.get("uid")
        path = row.get("path")
        digest = _digest(row.get("sha256"))
        if (
            not isinstance(uid, str)
            or uid not in _DASHBOARDS
            or uid in seen
            or not isinstance(path, str)
            or not path.startswith("build/observability-review/")
            or ".." in Path(path).parts
            or not re.fullmatch(r"[A-Za-z0-9._/-]+\.png", path)
            or digest is None
        ):
            continue
        seen.add(uid)
        screenshots.append(
            {
                "uid": uid,
                "title": _DASHBOARDS[uid],
                "path": path,
                "sha256": digest,
                "width": 1600,
                "height": 760,
            }
        )
    return {
        "required_dashboard_count": len(_DASHBOARDS),
        "captured_dashboard_count": len(screenshots),
        "all_five_captured": len(screenshots) == len(_DASHBOARDS),
        "capture_region": "top dashboard overview, 1600x760; log panels omitted",
        "screenshots": screenshots,
    }


def build_runtime_evidence(
    *,
    outcome: str,
    checkout_sha: object,
    source_sha: object,
    pr_head_sha: object,
    started_at_utc: object,
    finished_at_utc: object,
    elapsed_seconds: object,
    config_sha256: object,
    evidence: object,
    worker_alerts: object,
    screenshot_evidence: object,
) -> dict[str, object]:
    """Build the published artifact from explicit finite fields, never source dictionaries."""
    source = _mapping(evidence)
    alerts = _mapping(worker_alerts)
    return {
        "schema": RUNTIME_SCHEMA,
        "outcome": outcome if outcome in {"passed", "failed"} else "failed",
        "provenance": {
            "checkout_sha": _sha(checkout_sha),
            "source_sha": _sha(source_sha),
            "pr_head_sha": _sha(pr_head_sha),
        },
        "configuration_sha256": _project_config_hashes(config_sha256),
        "timing": {
            "ci_job_budget_seconds": JOB_TIMEOUT_MINUTES * 60,
            "collector_outage_minimum_seconds": COLLECTOR_OUTAGE_MINIMUM_SECONDS,
            "gateway_pipeline_alert_budget_seconds": GATEWAY_PIPELINE_ALERT_BUDGET_SECONDS,
            "started_at_utc": _timestamp(started_at_utc),
            "finished_at_utc": _timestamp(finished_at_utc),
            "elapsed_seconds": _number(elapsed_seconds, maximum=86_400),
            "worker_alerts": {
                name: {
                    "firing_budget_seconds": firing_budget,
                    "recovery_budget_seconds": recovery_budget,
                    "detection_elapsed_seconds": _number(
                        _mapping(_mapping(alerts.get(name)).get("firing")).get(
                            "detection_elapsed_seconds"
                        )
                    ),
                    "recovery_elapsed_seconds": _number(
                        _mapping(_mapping(alerts.get(name)).get("recovery")).get(
                            "recovery_elapsed_seconds"
                        )
                    ),
                }
                for name, (_severity, firing_budget, recovery_budget) in _WORKER_ALERTS.items()
            },
        },
        "route_attribution": _project_route_attribution(source),
        "gateway_sli": _project_gateway_sli(source),
        "collector_outage": _project_collector_outage(source),
        "alert_episodes": _project_worker_alerts(alerts),
        "business_recovery": _project_business_recovery(source, alerts),
        "dashboards": _project_screenshots(screenshot_evidence),
    }


def _safe_stage(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z0-9_]{1,80}", value):
        return value
    return "unknown"


def _safe_error_category(value: object) -> str | None:
    if value is None:
        return None
    name = value if isinstance(value, str) else type(value).__name__
    return _ERROR_CATEGORIES.get(name, "unexpected_error")


def build_failure_diagnostics(
    *,
    outcome: str,
    checkout_sha: object,
    source_sha: object,
    pr_head_sha: object,
    started_at_utc: object,
    elapsed_seconds: object,
    config_sha256: object = None,
    stage: object = None,
    error_type: object = None,
    evidence: object = None,
    worker_alerts: object = None,
    failure_context: object = None,
    screenshot_evidence: object = None,
) -> dict[str, object]:
    """Record bounded progress and failure category without exception text or raw logs."""
    alerts = _project_worker_alerts(worker_alerts)
    cases = _mapping(_mapping(evidence).get("business_outage_matrix"))
    screenshots = _project_screenshots(screenshot_evidence)
    return {
        "schema": FAILURE_SCHEMA,
        "outcome": outcome if outcome in {"passed", "failed"} else "failed",
        "failure_stage": _safe_stage(stage),
        "error_category": _safe_error_category(error_type),
        "provenance": {
            "checkout_sha": _sha(checkout_sha),
            "source_sha": _sha(source_sha),
            "pr_head_sha": _sha(pr_head_sha),
        },
        "configuration_sha256": _project_config_hashes(config_sha256),
        "timing": {
            "ci_job_budget_seconds": JOB_TIMEOUT_MINUTES * 60,
            "started_at_utc": _timestamp(started_at_utc),
            "elapsed_seconds": _number(elapsed_seconds, maximum=86_400),
        },
        "partial_progress": {
            "worker_alert_episodes_observed": sum(
                row["status"] != "not_observed" for row in alerts.values()
            ),
            "business_outage_cases_completed": [
                state for state in _BUSINESS_CASES if isinstance(cases.get(state), Mapping)
            ],
            "dashboard_screenshots_captured": screenshots["captured_dashboard_count"],
            "route_correlation_observed": _project_route_attribution(_mapping(evidence))[
                "gateway_request_log_matched"
            ],
            "collector_recovery_observed": _bool(
                _mapping(_mapping(evidence).get("collector_outage_isolation")),
                "collector_recovered",
            ),
        },
    }


def write_evidence_documents(
    run_directory: Path,
    *,
    runtime_document: Mapping[str, object],
    failure_document: Mapping[str, object],
) -> tuple[Path, Path]:
    """Persist only the two sanitized JSON files for an acceptance attempt."""
    run_directory.mkdir(parents=True, exist_ok=True)
    paths = (run_directory / "runtime-evidence.json", run_directory / "failure-diagnostics.json")
    documents = (runtime_document, failure_document)
    staged_paths: list[Path] = []
    try:
        for path, document in zip(paths, documents, strict=True):
            serialized = json.dumps(
                document, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False
            )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=run_directory,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                staged_paths.append(temporary_path)
                temporary_file.write(serialized + "\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

        for temporary_path, path in zip(tuple(staged_paths), paths, strict=True):
            os.replace(temporary_path, path)
            staged_paths.remove(temporary_path)
    finally:
        for temporary_path in staged_paths:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return paths
