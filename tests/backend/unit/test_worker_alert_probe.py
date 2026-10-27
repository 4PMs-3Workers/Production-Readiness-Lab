import pytest
from tests.containers import worker_alert_probe
from tests.containers.test_compose_acceptance import AcceptanceFailure
from tests.containers.test_telemetry_stack import (
    FullStackRuntime,
    new_alert_statuses_since_snapshot,
)
from tests.containers.worker_alert_probe import (
    MYSQL_EXEC_SCRIPT,
    MonotonicDeadline,
    active_worker_alert_identity,
    normalize_alertmanager_timestamp,
    pending_due_row_lock_sql,
)


def test_recovery_receiver_snapshot_precedes_immediate_resolved_delivery() -> None:
    firing = {
        "status": "firing",
        "severity": "warning",
        "received_at": "2026-10-27T01:00:00.000Z",
        "fingerprint": "0123456789abcdef",
        "starts_at": "2026-10-27T00:59:00.000Z",
    }
    resolved = {
        "status": "resolved",
        "severity": "warning",
        "received_at": "2026-10-27T01:01:00.000Z",
        "fingerprint": firing["fingerprint"],
        "starts_at": firing["starts_at"],
    }
    receiver_history = [firing]

    baseline = worker_alert_probe.capture_receiver_snapshot_before_recovery(
        lambda: list(receiver_history),
        lambda: receiver_history.append(resolved),
    )

    fresh_resolved = new_alert_statuses_since_snapshot(
        baseline,
        receiver_history,
        status="resolved",
        severity="warning",
    )
    assert fresh_resolved == [resolved]
    assert fresh_resolved[0]["fingerprint"] == firing["fingerprint"]
    assert fresh_resolved[0]["starts_at"] == firing["starts_at"]


def test_receiver_snapshot_failure_prevents_recovery() -> None:
    recovery_called = False

    def fail_snapshot() -> list[dict[str, str]]:
        raise RuntimeError("snapshot failed")

    def recover() -> None:
        nonlocal recovery_called
        recovery_called = True

    with pytest.raises(RuntimeError, match="snapshot failed"):
        worker_alert_probe.capture_receiver_snapshot_before_recovery(fail_snapshot, recover)

    assert not recovery_called


def test_alert_receiver_status_summary_preserves_alert_episode_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FullStackRuntime.__new__(FullStackRuntime)
    record = {
        "alertname": "WorkerHeartbeatStale",
        "severity": "critical",
        "status": "firing",
        "received_at": "2026-10-27T01:05:00.000Z",
        "fingerprint": "0123456789abcdef",
        "starts_at": "2026-10-27T01:04:05.000Z",
        "private_label": "must-not-escape",
    }
    monkeypatch.setattr(runtime, "api_json", lambda _url, **_kwargs: {"alerts": [record]})

    assert runtime.alert_receiver_statuses("WorkerHeartbeatStale") == [
        {
            "status": "firing",
            "severity": "critical",
            "received_at": record["received_at"],
            "fingerprint": record["fingerprint"],
            "starts_at": record["starts_at"],
        }
    ]


def test_receiver_snapshot_identity_distinguishes_worker_alert_episodes() -> None:
    first_episode = {
        "status": "firing",
        "severity": "critical",
        "received_at": "2026-10-27T01:05:00.000Z",
        "fingerprint": "0123456789abcdef",
        "starts_at": "2026-10-27T01:04:05.000Z",
    }
    second_episode = {
        **first_episode,
        "received_at": "2026-10-27T01:10:00.000Z",
        "starts_at": "2026-10-27T01:09:05.000Z",
    }

    assert new_alert_statuses_since_snapshot(
        [first_episode],
        [first_episode, second_episode],
        status="firing",
        severity="critical",
    ) == [second_episode]


def test_alertmanager_worker_identity_requires_expected_slot_labels() -> None:
    labels = {
        "alertname": "WorkerProgressStale",
        "severity": "critical",
        "service_name": "worker",
        "deployment_environment_name": "demo",
        "worker_slot": "1",
    }
    alert = {
        "labels": labels,
        "status": {"state": "active"},
        "fingerprint": "0123456789abcdef",
        "startsAt": "2026-10-27T03:04:05+02:00",
        "annotations": {"description": "not-retained"},
    }

    identity = active_worker_alert_identity(
        [alert], "WorkerProgressStale", severity="critical", worker_slot="1"
    )

    assert identity == {
        "fingerprint": "0123456789abcdef",
        "starts_at": "2026-10-27T01:04:05.000Z",
        "labels": labels,
    }
    assert (
        active_worker_alert_identity(
            [alert], "WorkerProgressStale", severity="critical", worker_slot="2"
        )
        is None
    )


def test_backlog_overdue_alert_identity_requires_no_worker_slot() -> None:
    alert = {
        "labels": {
            "alertname": "WorkerBacklogOverdue",
            "severity": "warning",
            "service_name": "worker",
            "deployment_environment_name": "demo",
        },
        "status": {"state": "active"},
        "fingerprint": "0123456789abcdef",
        "startsAt": "2026-10-27T03:04:05Z",
    }

    assert active_worker_alert_identity(
        [alert], "WorkerBacklogOverdue", severity="warning", worker_slot=None
    ) == {
        "fingerprint": "0123456789abcdef",
        "starts_at": "2026-10-27T03:04:05.000Z",
        "labels": alert["labels"],
    }
    alert["labels"]["worker_slot"] = "1"
    with pytest.raises(AcceptanceFailure, match="unexpectedly had a worker slot"):
        active_worker_alert_identity(
            [alert], "WorkerBacklogOverdue", severity="warning", worker_slot=None
        )


def test_alertmanager_timestamp_is_timezone_aware_and_bounded() -> None:
    assert (
        normalize_alertmanager_timestamp("2026-10-27T03:04:05.123456789+02:00")
        == "2026-10-27T01:04:05.123Z"
    )
    with pytest.raises(ValueError):
        normalize_alertmanager_timestamp("2026-10-27T03:04:05")
    with pytest.raises(ValueError):
        normalize_alertmanager_timestamp("x" * 41)


def test_due_proposal_lock_is_uuid_scoped_and_never_mutates_expiry() -> None:
    proposal_id = "00000000-0000-4000-8000-000000000001"
    statement, compact_id = pending_due_row_lock_sql(proposal_id)

    assert compact_id == proposal_id.replace("-", "")
    assert f"WHERE id = UNHEX(REPLACE('{proposal_id}', '-', ''))" in statement
    assert "status = 'PENDING' AND expires_at <= UTC_TIMESTAMP(6) FOR UPDATE" in statement
    assert "UPDATE change_proposals" not in statement
    assert "PRL_ROW_LOCK_READY" in statement
    assert MYSQL_EXEC_SCRIPT.startswith('MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql ')
    assert "--unbuffered" in MYSQL_EXEC_SCRIPT
    with pytest.raises(ValueError):
        pending_due_row_lock_sql("not-a-uuid")


def test_worker_alert_deadline_uses_finite_shared_monotonic_budget() -> None:
    deadline = MonotonicDeadline.after(3)

    assert 0 < deadline.remaining <= 3
    assert deadline.elapsed >= 0
    with pytest.raises(ValueError):
        MonotonicDeadline.after(float("inf"))
