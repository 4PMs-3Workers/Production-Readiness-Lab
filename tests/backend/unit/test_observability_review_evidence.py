from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
import tests.containers.observability_review_evidence as review_evidence
import yaml
from tests.containers.observability_review_evidence import (
    build_failure_diagnostics,
    build_runtime_evidence,
    collect_config_hashes,
    sanitize_alert_fingerprint,
    write_evidence_documents,
)

_MISSING = object()


@pytest.fixture
def workspace_build_tmp_path() -> Iterator[Path]:
    build_root = Path(__file__).resolve().parents[3] / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evidence-unit-", dir=build_root) as directory:
        yield Path(directory)


def _as_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _successful_business_case(state: str) -> dict[str, object]:
    return {
        "state": state,
        "application_readiness_http": 200,
        "gateway_readiness_http": 200,
        "authenticated_student_and_staff_sessions": True,
        "booking_replay_receipt_stable": True,
        "proposal_replay_receipt_stable": True,
        "proposal_accept_replay_stable": True,
        "successful_cancellation_route": 200,
        "accepted_business_effects": {"audit_event_count": 4, "notification_count": 8},
        "actual_worker_expiry": {
            "actual_worker_transition": "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
            "followup_scan_counter_before": 10,
            "followup_scan_counter_after": 11,
            "expiry_side_effects_stable_after_followup_scan": True,
        },
        "expired_business_effects": {"audit_event_count": 3, "notification_count": 6},
        "write_latency_max_ms": 123.456,
        "write_latency_count": 7,
    }


def _projected_business_recovery(
    evidence: dict[str, object], worker_alerts: dict[str, object] | None = None
) -> dict[str, object]:
    document = build_runtime_evidence(
        outcome="passed",
        checkout_sha="1" * 40,
        source_sha="2" * 40,
        pr_head_sha="3" * 40,
        started_at_utc="2026-10-27T02:00:00.000Z",
        finished_at_utc="2026-10-27T02:05:00.000Z",
        elapsed_seconds=300,
        config_sha256={},
        evidence=evidence,
        worker_alerts=worker_alerts or {},
        screenshot_evidence=None,
    )
    return _as_mapping(document["business_recovery"])


def _assert_successful_business_case(case_value: object) -> None:
    case = _as_mapping(case_value)
    assert case["application_ready"] is True
    assert case["gateway_ready"] is True
    assert case["authenticated_sessions"] is True
    assert case["booking_replay_stable"] is True
    assert case["proposal_replay_stable"] is True
    assert case["proposal_accept_replay_stable"] is True
    assert case["cancellation_succeeded"] is True
    assert case["accepted_audit_event_count"] == 4
    assert case["accepted_notification_count"] == 8
    assert case["proposal_expired_by_worker"] is True
    assert case["followup_scan_advanced"] is True
    assert case["expiry_effects_stable_after_followup_scan"] is True
    assert case["expired_audit_event_count"] == 3
    assert case["expired_notification_count"] == 6
    assert case["write_latency_max_ms"] == 123.456
    assert case["write_latency_count"] == 7


@pytest.mark.parametrize("service", ["loki", "tempo", "prometheus"])
def test_runtime_evidence_projects_backend_outage_business_case_wrapper(service: str) -> None:
    state = f"{service}_stopped"
    evidence = {
        "business_outage_matrix": {
            state: {
                "service": service,
                "stopped_container_preserved": True,
                "business_case": _successful_business_case(state),
                "outage_started_unix_ns": 1_800_000_000_000_000_000,
                "recovery_started_unix_ns": 1_800_000_100_000_000_000,
                "outage_elapsed_seconds": 100.0,
                "recovered_targets": {"prometheus": "up"},
            }
        }
    }

    recovery = _projected_business_recovery(evidence)
    outage_cases = _as_mapping(recovery["outage_cases"])

    _assert_successful_business_case(outage_cases[state])


def test_runtime_evidence_does_not_read_wrapper_when_nested_business_case_is_malformed() -> None:
    recovery = _projected_business_recovery(
        {
            "business_outage_matrix": {
                "loki_stopped": {
                    "application_readiness_http": 200,
                    "gateway_readiness_http": 200,
                    "business_case": None,
                }
            }
        }
    )
    outage_cases = _as_mapping(recovery["outage_cases"])

    assert "loki_stopped" not in outage_cases


@pytest.mark.parametrize("state", ["healthy", "collector_stopped"])
def test_runtime_evidence_keeps_flat_healthy_and_collector_business_cases(state: str) -> None:
    evidence = {"business_outage_matrix": {state: _successful_business_case(state)}}

    recovery = _projected_business_recovery(evidence)
    outage_cases = _as_mapping(recovery["outage_cases"])

    _assert_successful_business_case(outage_cases[state])


@pytest.mark.parametrize(
    ("duplicate_value", "expected"),
    [
        pytest.param(False, False, id="no-duplication"),
        pytest.param(True, True, id="duplication"),
        pytest.param(_MISSING, None, id="missing"),
        pytest.param(None, None, id="null"),
        pytest.param(0, None, id="falsy-non-bool"),
        pytest.param(1, None, id="truthy-non-bool"),
        pytest.param("", None, id="empty-string"),
        pytest.param("false", None, id="string"),
        pytest.param([], None, id="empty-list"),
    ],
)
def test_runtime_evidence_preserves_only_boolean_duplicate_effect_values(
    duplicate_value: object, expected: bool | None
) -> None:
    business_invariants: dict[str, object] = {
        "audit_expired_cancelled_reason": "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
        "proposal_expired_audits": 1,
        "proposal_expired_notifications": 2,
    }
    if duplicate_value is not _MISSING:
        business_invariants["later_scan_duplicated_effects"] = duplicate_value
    worker_alerts = {"WorkerBacklogOverdue": {"business_invariants": business_invariants}}

    recovery = _projected_business_recovery({}, worker_alerts)
    overdue = _as_mapping(recovery["overdue_alert_expiry"])

    assert overdue["later_scan_duplicated_effects"] is expected


def test_runtime_evidence_keeps_only_sanitized_route_sli_alert_and_business_facts(
    tmp_path: Path,
) -> None:
    request_id = "a" * 32
    trace_id = "b" * 32
    secret = "do-not-export-secret-canary"
    config_path = "tools/deploy/compose.observability.yaml"
    evidence = {
        "business_trace_correlation": {
            "api_log_route": "/api/v1/appointments",
            "gateway_request_log_matches": True,
            "api_request_log_matches": True,
            "tempo_trace_stored": True,
            "mysql_db_query_span_present": True,
            "sql_statement_and_parameter_attributes_absent": True,
            "request_id": request_id,
            "trace_id": trace_id,
            "raw_body": secret,
        },
        "gateway_loki_event": {
            "event": "gateway.request",
            "request_id_match": True,
            "trace_id_format_ok": True,
            "index_series_count": 1,
            "raw_request": secret,
        },
        "gateway_sli_truth_table": {
            "availability_success_over_eligible": 0.9,
            "fast_success_inclusive_500ms": 17 / 18,
            "all_errors_availability_zero": 0.0,
            "empty_eligible_window_has_data": False,
            "genuine_gateway_healthchecks_in_1m": 2,
            "pushed_gateway_json_events": 121,
            "fixture_path": secret,
        },
        "collector_outage_isolation": {
            "collector_stopped_status": "exited",
            "collector_container_preserved": True,
            "gateway_ready_while_collector_stopped": True,
            "collector_outage_elapsed_seconds": 96.5,
            "collector_recovered": True,
        },
        "gateway_request_denominators_at_heartbeat_expiry": {"availability": 22.0},
        "gateway_sli_samples_after_heartbeat_expiry": {
            "ratio_samples_absent": True,
            "fast_success_alert_predicate_absent": True,
        },
        "gateway_sli_samples_after_collector_recovery": {"ratio_samples_returned": True},
        "genuine_gateway_healthchecks_after_collector_recovery": {"observed_events": 2},
        "collector_pipeline_missing_firing": {
            "alertname": "GatewayPipelineHeartbeatMissing",
            "receiver_statuses": [{"status": "firing", "severity": "critical"}],
        },
        "collector_pipeline_missing_resolved": {
            "alertname": "GatewayPipelineHeartbeatMissing",
            "receiver_statuses": [{"status": "resolved", "severity": "critical"}],
        },
        "collector_target_warning_firing": {
            "alertname": "TelemetryTargetDown",
            "receiver_first_observed_unix_ns": 1_800_000_000_000_000_000,
        },
        "collector_target_warning_resolved": {
            "alertname": "TelemetryTargetDown",
            "receiver_first_observed_unix_ns": 1_800_000_100_000_000_000,
        },
        "collector_outage_cumulative_loss": {"delta": {"failed": 3.0, "dropped": 2.0}},
        "business_outage_matrix": {
            "collector_stopped": {
                "application_readiness_http": 200,
                "gateway_readiness_http": 200,
                "authenticated_student_and_staff_sessions": True,
                "booking_replay_receipt_stable": True,
                "accepted_business_effects": {
                    "audit_event_count": 4,
                    "notification_count": 8,
                    "raw_payload": secret,
                },
                "expired_business_effects": {"audit_event_count": 3, "notification_count": 6},
                "actual_worker_expiry": {
                    "actual_worker_transition": "EXPIRED:CANCELLED:CHANGE_PROPOSAL_EXPIRED",
                    "followup_scan_counter_before": 10,
                    "followup_scan_counter_after": 11,
                    "expiry_side_effects_stable_after_followup_scan": True,
                    "worker_id": secret,
                },
                "write_latency_max_ms": 123.4,
                "write_latency_count": 7,
                "request_id": request_id,
            }
        },
        "new_owner_database_recovery": {
            "first_healthy_scan_counter_after_recovery": 4,
            "later_healthy_scan_counter_after_recovery": 5,
            "scan_counter_before_expiry_followup": 5,
            "later_scan_counter_after_expiry": 6,
            "worker_process_changed_from_pre_restart": True,
            "series_identity_matches_stopped_baseline": True,
            "scan_advanced_since_stopped_db_baseline": True,
            "private_id": secret,
        },
    }
    worker_alerts = {
        "WorkerHeartbeatStale": {
            "firing": {
                "fingerprint": "f" * 32,
                "starts_at": "2026-10-27T02:00:00.000Z",
                "receiver_delivery": {
                    "status": "firing",
                    "severity": "critical",
                    "received_at": "2026-10-27T02:00:30.000Z",
                    "fingerprint": "f" * 32,
                    "starts_at": "2026-10-27T02:00:00.000Z",
                    "raw_annotation": secret,
                },
                "detection_elapsed_seconds": 30.5,
                "poll_count": 7,
                "raw_alert_payload": secret,
            },
            "recovery": {
                "fingerprint": "f" * 32,
                "starts_at": "2026-10-27T02:00:00.000Z",
                "receiver_delivery": {
                    "status": "resolved",
                    "severity": "critical",
                    "received_at": "2026-10-27T02:01:00.000Z",
                    "fingerprint": "f" * 32,
                    "starts_at": "2026-10-27T02:00:00.000Z",
                },
                "prometheus_predicate_cleared": True,
                "prometheus_firing_cleared": True,
                "real_recovery_observed": True,
                "recovery_elapsed_seconds": 45.0,
                "poll_count": 9,
            },
        }
    }
    config_hashes = {config_path: "c" * 64}

    document = build_runtime_evidence(
        outcome="passed",
        checkout_sha="1" * 40,
        source_sha="2" * 40,
        pr_head_sha="3" * 40,
        started_at_utc="2026-10-27T02:00:00.000Z",
        finished_at_utc="2026-10-27T02:02:00.000Z",
        elapsed_seconds=120.0,
        config_sha256=config_hashes,
        evidence=evidence,
        worker_alerts=worker_alerts,
        screenshot_evidence={
            "screenshots": [
                {
                    "uid": uid,
                    "path": f"build/observability-review/run-abc/screenshots/run-def/{uid}.png",
                    "sha256": "d" * 64,
                    "title": secret,
                    "dashboard_path": secret,
                }
                for uid in (
                    "prl-gateway",
                    "prl-api-errors",
                    "prl-db-pool",
                    "prl-worker-progress",
                    "prl-telemetry-loss",
                )
            ]
            + [
                {
                    "uid": "not-allowlisted",
                    "path": "build/observability-review/../../secret.png",
                    "sha256": "e" * 64,
                }
            ]
        },
    )
    encoded = json.dumps(document, sort_keys=True)

    assert document["schema"] == "prl-observability-review-runtime-v1"
    assert document["provenance"] == {
        "checkout_sha": "1" * 40,
        "source_sha": "2" * 40,
        "pr_head_sha": "3" * 40,
    }
    route_attribution = _as_mapping(document["route_attribution"])
    gateway_sli = _as_mapping(document["gateway_sli"])
    collector_outage = _as_mapping(document["collector_outage"])
    alert_episodes = _as_mapping(document["alert_episodes"])
    heartbeat_stale = _as_mapping(alert_episodes["WorkerHeartbeatStale"])
    dashboards = _as_mapping(document["dashboards"])
    assert route_attribution["api_route"] == "/api/v1/appointments"
    assert route_attribution["gateway_request_log_matched"] is True
    assert gateway_sli["empty_window_is_unknown"] is True
    assert collector_outage["gateway_sli_absent_after_heartbeat_expiry"] is True
    assert collector_outage["gateway_sli_restored_after_recovery"] is True
    assert collector_outage["minimum_outage_seconds"] == 90
    assert gateway_sli["alertname"] == "GatewayFastSuccessBelowSLO"
    assert heartbeat_stale["fingerprint"] == "f" * 32
    assert heartbeat_stale["alertname"] == "WorkerHeartbeatStale"
    assert heartbeat_stale["firing_budget_seconds"] == 90
    assert dashboards["all_five_captured"] is True
    assert dashboards["captured_dashboard_count"] == 5
    screenshot_rows = dashboards["screenshots"]
    assert isinstance(screenshot_rows, list)
    for screenshot in screenshot_rows:
        screenshot_path = _as_mapping(screenshot)["path"]
        assert isinstance(screenshot_path, str)
        assert screenshot_path.startswith("build/observability-review/")
    business_recovery = _as_mapping(document["business_recovery"])
    outage_cases = _as_mapping(business_recovery["outage_cases"])
    collector_case = _as_mapping(outage_cases["collector_stopped"])
    configuration_hashes = _as_mapping(document["configuration_sha256"])
    assert collector_case["expired_audit_event_count"] == 3
    assert collector_case["followup_scan_advanced"] is True
    assert configuration_hashes["tools/infra/images.lock.json"] is None
    for forbidden in (request_id, trace_id, secret, "raw_alert_payload", "raw_body"):
        assert forbidden not in encoded


def test_failure_diagnostics_are_written_with_sanitized_error_category(
    workspace_build_tmp_path: Path,
) -> None:
    secret = "never-write-this-payload"
    evidence = {"business_trace_correlation": {"request_id": "a" * 32, "raw": secret}}
    failure_context = {
        "stage": "collector_restart_gateway_event_query",
        "request_id": "a" * 32,
        "trace_id": "b" * 32,
        "exception_message": secret,
    }
    diagnostic = build_failure_diagnostics(
        outcome="failed",
        checkout_sha="1" * 40,
        source_sha="2" * 40,
        pr_head_sha="3" * 40,
        started_at_utc="2026-10-27T02:00:00.000Z",
        elapsed_seconds=12.0,
        stage=failure_context["stage"],
        error_type="AcceptanceFailure",
        evidence=evidence,
        worker_alerts={},
        failure_context=failure_context,
        screenshot_evidence=None,
    )
    runtime_document = build_runtime_evidence(
        outcome="failed",
        checkout_sha="1" * 40,
        source_sha="2" * 40,
        pr_head_sha="3" * 40,
        started_at_utc="2026-10-27T02:00:00.000Z",
        finished_at_utc="2026-10-27T02:00:12.000Z",
        elapsed_seconds=12.0,
        config_sha256={},
        evidence=evidence,
        worker_alerts={},
        screenshot_evidence=None,
    )
    runtime_path, failure_path = write_evidence_documents(
        workspace_build_tmp_path / "run-20261027T020000Z-deadbeef",
        runtime_document=runtime_document,
        failure_document=diagnostic,
    )
    encoded = runtime_path.read_text(encoding="utf-8") + failure_path.read_text(encoding="utf-8")

    assert diagnostic["schema"] == "prl-observability-review-failure-v1"
    assert diagnostic["error_category"] == "acceptance_failure"
    assert diagnostic["failure_stage"] == "collector_restart_gateway_event_query"
    assert secret not in encoded
    assert "a" * 32 not in encoded and "b" * 32 not in encoded
    assert {path.suffix for path in (runtime_path, failure_path)} == {".json"}


def test_config_hashes_use_fixed_allowlist_and_fail_closed_for_missing_files(
    tmp_path: Path,
) -> None:
    relative = "tools/deploy/compose.observability.yaml"
    config = tmp_path / relative
    config.parent.mkdir(parents=True)
    config.write_bytes(b"services: {}\n")

    hashes = collect_config_hashes(tmp_path)

    assert hashes[relative] == "fa6ccea1ca4e3a031d9e99f25cc05db803aa9bac642c000ddab14f6d9da54b52"
    assert all("secret" not in name.lower() for name in hashes)
    assert hashes["tools/infra/images.lock.json"] is None


def test_containerized_demo_upload_is_limited_to_sanitized_review_artifacts() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow_path = root / ".github/workflows/containerized-demo.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["compose-acceptance"]

    assert job["timeout-minutes"] == 45
    assert job["env"]["PR_HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert {
        "RUN_CONTAINER_TESTS",
        "RUN_COMPOSE_TESTS",
        "RUN_GATEWAY_TESTS",
        "RUN_COMPOSE_ACCEPTANCE",
        "RUN_OBSERVABILITY_TESTS",
    }.issubset(job["env"])
    assert "if skipped:" in workflow_text
    assert "if: always()" in workflow_text

    uploader = next(
        step for step in job["steps"] if step.get("uses") == "actions/upload-artifact@v7"
    )
    assert uploader["if"] == "always()"
    assert uploader["with"]["name"] == (
        "observability-review-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert uploader["with"]["path"].splitlines() == [
        "build/observability-review/**/*.json",
        "build/observability-review/**/*.png",
    ]
    assert uploader["with"]["if-no-files-found"] == "warn"
    assert uploader["with"]["retention-days"] == 7
    assert all(
        forbidden not in uploader["with"]["path"].lower()
        for forbidden in ("junit", "xml", "log", "env")
    )


def test_alertmanager_fingerprint_accepts_only_bounded_protocol_lengths() -> None:
    for length in (16, 32, 64):
        value = "a" * length
        assert sanitize_alert_fingerprint(value) == value
    assert sanitize_alert_fingerprint("a" * 15) is None
    assert sanitize_alert_fingerprint("a" * 65) is None
    assert sanitize_alert_fingerprint("g" * 32) is None


def test_all_worker_alert_episodes_project_matching_firing_and_recovery_identity() -> None:
    secret = "raw-alert-payload-canary"
    expected = {
        "WorkerHeartbeatStale": ("critical", "1"),
        "WorkerHeartbeatMissing": ("critical", "1"),
        "WorkerProgressStale": ("critical", "1"),
        "WorkerBacklogSnapshotStale": ("warning", "1"),
        "WorkerBacklogOverdue": ("warning", None),
    }
    worker_alerts = {}
    identities = {}
    for index, (name, (severity, _slot)) in enumerate(expected.items(), start=1):
        fingerprint = str(index) * 32
        starts_at = f"2026-10-27T02:00:0{index}.000Z"
        firing_received_at = f"2026-10-27T02:00:3{index}.000Z"
        recovery_received_at = f"2026-10-27T02:01:0{index}.000Z"
        identities[name] = (fingerprint, starts_at, firing_received_at, recovery_received_at)

        def receiver_delivery(
            status: str,
            received_at: str,
            *,
            expected_severity: str = severity,
            expected_fingerprint: str = fingerprint,
            expected_starts_at: str = starts_at,
        ) -> dict[str, str]:
            return {
                "status": status,
                "severity": expected_severity,
                "fingerprint": expected_fingerprint,
                "starts_at": expected_starts_at,
                "received_at": received_at,
                "raw_payload": secret,
            }

        worker_alerts[name] = {
            "firing": {
                "fingerprint": fingerprint,
                "starts_at": starts_at,
                "receiver_delivery": receiver_delivery("firing", firing_received_at),
                "detection_elapsed_seconds": 20.0,
                "poll_count": 4,
                "labels": {"request_id": secret},
            },
            "recovery": {
                "fingerprint": fingerprint,
                "starts_at": starts_at,
                "receiver_delivery": receiver_delivery("resolved", recovery_received_at),
                "recovery_elapsed_seconds": 30.0,
                "poll_count": 5,
                "annotations": secret,
            },
        }

    def project(alert_observations: object) -> dict[str, object]:
        return build_runtime_evidence(
            outcome="passed",
            checkout_sha="1" * 40,
            source_sha="2" * 40,
            pr_head_sha="3" * 40,
            started_at_utc="2026-10-27T02:00:00.000Z",
            finished_at_utc="2026-10-27T02:05:00.000Z",
            elapsed_seconds=300,
            config_sha256={},
            evidence={},
            worker_alerts=alert_observations,
            screenshot_evidence=None,
        )

    document = project(worker_alerts)
    encoded = json.dumps(document, sort_keys=True)
    projected = cast(dict[str, dict[str, object]], document["alert_episodes"])
    assert set(projected) == set(expected)
    for name, (severity, worker_slot) in expected.items():
        fingerprint, starts_at, firing_received_at, recovery_received_at = identities[name]
        episode = projected[name]
        assert episode["alertname"] == name
        assert episode["severity"] == severity
        assert episode["worker_slot"] == worker_slot
        assert episode["status"] == "resolved"
        assert episode["fingerprint"] == fingerprint
        assert episode["starts_at_utc"] == starts_at
        assert episode["firing_receiver_received_at_utc"] == firing_received_at
        assert episode["recovery_receiver_received_at_utc"] == recovery_received_at
    assert secret not in encoded

    mismatched_recovery = json.loads(json.dumps(worker_alerts))
    recovery_fingerprint = "e" * 32
    mismatched_recovery["WorkerHeartbeatStale"]["recovery"]["fingerprint"] = recovery_fingerprint
    recovery_document = project(mismatched_recovery)
    recovery_episodes = cast(dict[str, dict[str, object]], recovery_document["alert_episodes"])
    recovery_episode = recovery_episodes["WorkerHeartbeatStale"]
    assert recovery_episode["status"] == "firing"
    assert recovery_episode["recovery_receiver_received_at_utc"] is None
    assert recovery_fingerprint not in json.dumps(recovery_document, sort_keys=True)

    mismatched_firing = json.loads(json.dumps(worker_alerts))
    firing_fingerprint = "f" * 32
    mismatched_firing["WorkerProgressStale"]["firing"]["fingerprint"] = firing_fingerprint
    firing_document = project(mismatched_firing)
    firing_episodes = cast(dict[str, dict[str, object]], firing_document["alert_episodes"])
    firing_episode = firing_episodes["WorkerProgressStale"]
    assert firing_episode["status"] == "not_observed"
    assert firing_episode["fingerprint"] is None
    assert firing_episode["firing_receiver_received_at_utc"] is None
    assert firing_episode["recovery_receiver_received_at_utc"] is None
    assert firing_fingerprint not in json.dumps(firing_document, sort_keys=True)


def test_writer_cleans_staged_files_when_atomic_replace_fails(
    workspace_build_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_directory = workspace_build_tmp_path / "run-interrupted"
    run_directory.mkdir()
    runtime_path = run_directory / "runtime-evidence.json"
    failure_path = run_directory / "failure-diagnostics.json"
    runtime_path.write_text("previous runtime\n", encoding="utf-8")
    failure_path.write_text("previous failure\n", encoding="utf-8")
    real_replace = review_evidence.os.replace
    replace_calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated interrupted replacement")
        real_replace(source, destination)

    monkeypatch.setattr(review_evidence.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="simulated interrupted replacement"):
        write_evidence_documents(
            run_directory,
            runtime_document={"schema": "runtime-new"},
            failure_document={"schema": "failure-new"},
        )

    assert json.loads(runtime_path.read_text(encoding="utf-8")) == {"schema": "runtime-new"}
    assert failure_path.read_text(encoding="utf-8") == "previous failure\n"
    assert not [path for path in run_directory.iterdir() if path.suffix == ".tmp"]
