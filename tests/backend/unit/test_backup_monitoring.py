import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

from tests.backend.unit.test_backup_storage import fixture_artifact, populate

from app.backup.locking import operation_lock
from app.backup.monitoring import (
    BackupMonitorState,
    collect_initial_state,
    read_state,
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

    state = collect_initial_state(config, now=datetime.now(UTC))

    assert state.last_verified_backup_at == datetime.fromisoformat(
        published.completed_at.replace("Z", "+00:00")
    )
    assert state.artifact_integrity_verified is True
    assert state.application_rehearsal_verified is False


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
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=5
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
