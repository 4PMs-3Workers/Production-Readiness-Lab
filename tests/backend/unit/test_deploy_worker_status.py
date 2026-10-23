"""Fresh worker-progress parsing and snapshot tracking contracts."""

import json

import pytest
from tools.deploy.worker_status import (
    WorkerProgress,
    WorkerProgressTracker,
    latest_worker_progress,
)

WORKER_A = "a" * 32
WORKER_B = "b" * 32
AWARE_OLD = "2000-01-01T00:00:00Z"


def event(kind="heartbeat", **updates):
    record = {
        "event": kind,
        "worker_id": WORKER_A,
        "phase": "running",
        "database_ok": True,
        "successful_scans": 2,
        "last_successful_scan": AWARE_OLD,
    }
    record.update(updates)
    return json.dumps(record)


def output(*records):
    return "\n".join(records)


@pytest.mark.parametrize("kind", ["heartbeat", "scan", "candidate", "ready"])
def test_acceptance_accepts_latest_healthy_progress_events(kind):
    progress = latest_worker_progress(output(event(kind)), require_heartbeat=False)

    assert progress == WorkerProgress(WORKER_A, 2)


def test_cli_requires_a_heartbeat_event():
    assert latest_worker_progress(output(event("scan")), require_heartbeat=True) is None
    assert latest_worker_progress(
        output(event("heartbeat")), require_heartbeat=True
    ) == WorkerProgress(WORKER_A, 2)


def test_old_aware_database_timestamp_does_not_define_freshness():
    progress = latest_worker_progress(
        output(event(last_successful_scan=AWARE_OLD)), require_heartbeat=True
    )

    assert progress == WorkerProgress(WORKER_A, 2)


@pytest.mark.parametrize("terminal", ["stopping", "stopped", "fatal"])
def test_terminal_record_blocks_an_older_healthy_heartbeat(terminal):
    assert (
        latest_worker_progress(
            output(event(), event(terminal, phase=terminal, database_ok=False)),
            require_heartbeat=False,
        )
        is None
    )


def test_unhealthy_record_blocks_an_older_healthy_heartbeat():
    assert (
        latest_worker_progress(
            output(event(), event("scan", database_ok=False)), require_heartbeat=False
        )
        is None
    )


def test_new_process_starting_record_blocks_an_old_process_heartbeat():
    starting = event(
        "starting",
        worker_id=WORKER_B,
        phase="starting",
        database_ok=False,
        successful_scans=0,
        last_successful_scan=None,
    )

    assert latest_worker_progress(output(event(), starting), require_heartbeat=False) is None


def test_malformed_newer_record_blocks_an_older_healthy_heartbeat():
    assert latest_worker_progress(output(event(), "{broken json"), require_heartbeat=False) is None


@pytest.mark.parametrize("scans", [True, False, 0, -1, 1.5, "2", None])
def test_boolean_or_invalid_scan_counter_is_rejected(scans):
    assert (
        latest_worker_progress(output(event(successful_scans=scans)), require_heartbeat=False)
        is None
    )


@pytest.mark.parametrize("database_ok", [False, 1, "true", None])
def test_database_health_must_be_exactly_true(database_ok):
    assert (
        latest_worker_progress(output(event(database_ok=database_ok)), require_heartbeat=False)
        is None
    )


@pytest.mark.parametrize(
    "timestamp",
    ["2000-01-01T00:00:00", "not-a-timestamp", None, 123],
)
def test_missing_naive_or_malformed_database_timestamp_is_rejected(timestamp):
    assert (
        latest_worker_progress(
            output(event(last_successful_scan=timestamp)), require_heartbeat=False
        )
        is None
    )


@pytest.mark.parametrize("worker_id", ["A" * 32, "a" * 31, "a" * 32 + "0", "not-a-uuid"])
def test_invalid_worker_identity_is_rejected(worker_id):
    assert (
        latest_worker_progress(output(event(worker_id=worker_id)), require_heartbeat=False) is None
    )


def test_parser_returns_new_process_progress_without_crossing_older_identity():
    newest = event("scan", worker_id=WORKER_B, successful_scans=1)

    assert latest_worker_progress(
        output(event(), newest), require_heartbeat=False
    ) == WorkerProgress(WORKER_B, 1)


def test_tracker_requires_a_later_higher_scan_count_from_same_worker():
    tracker = WorkerProgressTracker()
    progress = WorkerProgress(WORKER_A, 5)

    assert tracker.observe(progress, running=True) is False
    assert tracker.observe(progress, running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_A, 6), running=True) is True
    assert tracker.observe(WorkerProgress(WORKER_A, 6), running=True) is False


def test_tracker_uses_new_process_baseline_without_comparing_reset_counts():
    tracker = WorkerProgressTracker()

    assert tracker.observe(WorkerProgress(WORKER_A, 50), running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_B, 1), running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_B, 2), running=True) is True


def test_tracker_resets_baseline_when_worker_is_not_running_or_has_no_progress():
    tracker = WorkerProgressTracker()

    assert tracker.observe(WorkerProgress(WORKER_A, 5), running=True) is False
    assert tracker.observe(None, running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_A, 6), running=True) is False
    assert tracker.observe(None, running=False) is False
    assert tracker.observe(WorkerProgress(WORKER_A, 7), running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_A, 8), running=True) is True


def test_tracker_excludes_the_worker_that_was_stopped_for_recovery():
    tracker = WorkerProgressTracker(excluded_worker_id=WORKER_A)

    assert tracker.observe(WorkerProgress(WORKER_A, 100), running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_B, 1), running=True) is False
    assert tracker.observe(WorkerProgress(WORKER_B, 2), running=True) is True
