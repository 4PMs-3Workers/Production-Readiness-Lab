import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

from tests.backend.unit.test_backup_storage import fixture_artifact, populate

from app.backup.config import Reason
from app.backup.locking import operation_lock
from app.backup.monitoring import (
    BackupMonitorState,
    collect_initial_state,
    read_state,
    record_job_finish,
    record_job_start,
    write_state,
)
from app.backup.storage import staging


def make_state(now=None):
    now = now or datetime.now(UTC)
    return BackupMonitorState(
        schema_version=1,
        updated_at=now,
        last_verified_backup_at=None,
        artifact_integrity_verified=None,
        application_rehearsal_verified=False,
        last_job_operation=None,
        last_job_started_at=None,
        last_job_finished_at=None,
        last_job_result=None,
        last_job_reason=None,
        last_job_duration_seconds=None,
        minimum_free_bytes=None,
        remote_copy=None,
    )


def test_monitor_state_round_trip_rejects_extra_fields_and_oversize(tmp_path):
    path = tmp_path / ".prl-monitoring-state.json"
    expected = make_state()

    write_state(path, expected)

    assert read_state(path) == expected
    path.write_text(json.dumps({**expected.model_dump(mode="json"), "unexpected": True}))
    assert read_state(path) is None
    path.write_bytes(b" " * 65_537)
    assert read_state(path) is None


def test_initial_snapshot_uses_latest_integrity_verified_artifact(tmp_path):
    config, manifest = fixture_artifact(tmp_path)
    with staging(config, manifest["backup_id"]) as owned:
        populate(owned, manifest)
        published = owned.publish(manifest)

    initialized_at = datetime.now(UTC)
    state = collect_initial_state(config, now=initialized_at)

    assert state.last_verified_backup_at == datetime.fromisoformat(
        published.completed_at.replace("Z", "+00:00")
    )
    assert state.artifact_integrity_verified is True
    assert state.application_rehearsal_verified is False
    assert state.monitor_initialized_at == initialized_at


def test_empty_or_corrupt_store_is_unknown(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    assert collect_initial_state(config, now=datetime.now(UTC)).artifact_integrity_verified is None

    _, manifest = fixture_artifact(tmp_path / "other")
    other_config = config.__class__(config.database_url, tmp_path / "other", min_free_bytes=0)
    with staging(other_config, manifest["backup_id"]) as owned:
        populate(owned, manifest)
        owned.publish(manifest)
    (tmp_path / "other" / manifest["backup_id"] / "COMPLETE").write_text("bad")

    state = collect_initial_state(other_config, now=datetime.now(UTC))
    assert state.last_verified_backup_at is None
    assert state.artifact_integrity_verified is None


def test_initial_snapshot_accepts_any_zero_offset_utc_timezone(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    now = datetime.now(timezone(timedelta(0), name="UTC"))

    state = collect_initial_state(config, now=now)

    assert state.updated_at == now


def test_initial_scan_does_not_wait_for_operation_lock(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    script = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from app.backup.config import BackupConfig
from app.backup.monitoring import collect_initial_state
config = BackupConfig('mysql+asyncmy://x:p@host/prl', Path(sys.argv[1]))
collect_initial_state(config, now=datetime.now(UTC))
"""
    with operation_lock(config):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=15
        )
    assert result.returncode == 0 and result.stderr == b""


def test_initial_scan_rejects_future_snapshot_time(tmp_path):
    value = make_state().model_dump(mode="json")
    value["updated_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path = tmp_path / ".prl-monitoring-state.json"
    path.write_text(json.dumps(value))
    assert read_state(path) is None


def test_state_lock_cleanup_failure_does_not_escape(tmp_path, monkeypatch):
    from app.backup import monitoring

    path = tmp_path / ".prl-monitoring-state.json"

    def fail_unlock(_descriptor):
        raise OSError("lock release failed")

    monkeypatch.setattr(monitoring, "_unlock", fail_unlock)

    write_state(path, make_state())

    assert read_state(path) is not None


def test_scheduled_success_records_artifact_and_job_success_separately(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    completed = datetime.now(UTC)
    started = completed - timedelta(seconds=2)

    record_job_start(config, operation="scheduled", started_at=started)
    record_job_finish(
        config,
        operation="scheduled",
        finished_at=completed,
        success=True,
        reason=None,
        duration_seconds=2.0,
        backup_completed_at=completed,
        artifact_integrity_verified=True,
    )

    state = read_state(tmp_path / ".prl-monitoring-state.json")
    assert state is not None
    assert state.last_verified_backup_at == completed
    assert state.artifact_integrity_verified is True
    assert state.last_job_result == "success"
    assert state.scheduled_job_failure_pending is False


def test_retention_failure_preserves_new_artifact_but_records_job_failure(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    completed = datetime.now(UTC)
    started = completed - timedelta(seconds=2)
    record_job_start(config, operation="scheduled", started_at=started)

    record_job_finish(
        config,
        operation="scheduled",
        finished_at=completed,
        success=False,
        reason=Reason.STORAGE,
        duration_seconds=2.0,
        backup_completed_at=completed,
        artifact_integrity_verified=True,
    )

    state = read_state(tmp_path / ".prl-monitoring-state.json")
    assert state is not None
    assert state.last_verified_backup_at == completed
    assert state.artifact_integrity_verified is True
    assert state.last_job_result == "failure"
    assert state.scheduled_job_failure_pending is True


def test_failed_job_uses_finite_reason_without_raw_exception(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    started = datetime.now(UTC) - timedelta(seconds=2)
    record_job_start(config, operation="scheduled", started_at=started)

    record_job_finish(
        config,
        operation="scheduled",
        finished_at=started + timedelta(seconds=1),
        success=False,
        reason=Reason.DATABASE,
        duration_seconds=1.0,
        backup_completed_at=None,
        artifact_integrity_verified=None,
    )

    raw = (tmp_path / ".prl-monitoring-state.json").read_text()
    state = read_state(tmp_path / ".prl-monitoring-state.json")
    assert state is not None and state.last_job_reason == Reason.DATABASE
    assert "DATABASE_OPERATION_FAILED" in raw
    assert "password" not in raw and "traceback" not in raw


def test_state_write_failure_does_not_fail_backup(tmp_path, monkeypatch):
    from app.backup import monitoring

    config, _ = fixture_artifact(tmp_path)
    monkeypatch.setattr(monitoring, "write_state", lambda *args: (_ for _ in ()).throw(OSError()))

    record_job_start(config, operation="backup", started_at=datetime.now(UTC))


def test_missing_final_report_leaves_start_without_fabricated_success(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    started = datetime.now(UTC)

    record_job_start(config, operation="scheduled", started_at=started)

    state = read_state(tmp_path / ".prl-monitoring-state.json")
    assert state is not None
    assert state.last_job_started_at == started
    assert state.last_job_finished_at is None
    assert state.last_job_result is None


def test_manual_backup_success_does_not_clear_scheduled_job_failure(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    started = datetime.now(UTC) - timedelta(seconds=10)
    record_job_start(config, operation="scheduled", started_at=started)
    record_job_finish(
        config,
        operation="scheduled",
        finished_at=started + timedelta(seconds=1),
        success=False,
        reason=Reason.DATABASE,
        duration_seconds=1.0,
        backup_completed_at=None,
        artifact_integrity_verified=None,
    )
    next_started = started + timedelta(seconds=2)
    record_job_start(config, operation="backup", started_at=next_started)
    record_job_finish(
        config,
        operation="backup",
        finished_at=next_started + timedelta(seconds=1),
        success=True,
        reason=None,
        duration_seconds=1.0,
        backup_completed_at=next_started + timedelta(seconds=1),
        artifact_integrity_verified=True,
    )

    state = read_state(tmp_path / ".prl-monitoring-state.json")
    assert state is not None and state.scheduled_job_failure_pending is True


def test_manual_job_does_not_overwrite_scheduled_lifecycle_timestamps(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    scheduled_started = datetime.now(UTC) - timedelta(hours=2)
    scheduled_finished = scheduled_started + timedelta(minutes=1)
    manual_started = scheduled_finished + timedelta(minutes=1)
    record_job_start(config, operation="scheduled", started_at=scheduled_started)
    record_job_finish(
        config,
        operation="scheduled",
        finished_at=scheduled_finished,
        success=True,
        reason=None,
        duration_seconds=60,
        backup_completed_at=None,
        artifact_integrity_verified=None,
    )
    record_job_start(config, operation="backup", started_at=manual_started)
    record_job_finish(
        config,
        operation="backup",
        finished_at=manual_started + timedelta(seconds=30),
        success=True,
        reason=None,
        duration_seconds=30,
        backup_completed_at=None,
        artifact_integrity_verified=None,
    )

    state = read_state(config.store / ".prl-monitoring-state.json")

    assert state is not None
    assert state.last_scheduled_job_started_at == scheduled_started
    assert state.last_scheduled_job_finished_at == scheduled_finished
    assert state.last_scheduled_job_result == "success"
    assert state.last_scheduled_job_duration_seconds == 60


def test_monitor_init_fails_when_state_cannot_be_persisted(tmp_path, monkeypatch):
    from app.backup import __main__ as backup_cli

    config, _ = fixture_artifact(tmp_path)
    reports = []

    class FakePublisher:
        def submit(self, report):
            reports.append(json.loads(report))

        async def close(self, _deadline):
            return None

    monkeypatch.setattr(backup_cli, "Publisher", FakePublisher)
    monkeypatch.setattr(backup_cli, "load_config", lambda: config)
    monkeypatch.setattr(backup_cli, "write_state", lambda *args: None)

    result = asyncio.run(backup_cli.execute(["monitor-init"]))

    assert result == 1
    assert reports[-1]["operation"] == "monitor-init"
    assert reports[-1]["success"] is False
