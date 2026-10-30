import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tests.backend.unit.test_backup_storage import fixture_artifact, populate

from app.backup.locking import operation_lock
from app.backup.retention import retain, status
from app.backup.storage import staging


def artifact(tmp_path, completed, server="00000000-0000-0000-0000-000000000001"):
    config, value = fixture_artifact(tmp_path)
    value["source"]["server_uuid"] = server
    with staging(config, value["backup_id"]) as owned:
        populate(owned, value)
        owned.publish(value)
    path = tmp_path / value["backup_id"]
    value = json.loads((path / "manifest.json").read_bytes())
    value["completed_at"] = completed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    value["started_at"] = (
        (completed - timedelta(seconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    raw = json.dumps(value).encode()
    (path / "manifest.json").write_bytes(raw)
    (path / "COMPLETE").write_bytes(hashlib.sha256(raw).hexdigest().encode())
    return config, value["backup_id"]


def test_retention_preserves_last_success_per_source_and_never_deletes_unrelated(tmp_path):
    now = datetime.now(UTC)
    config, newest = artifact(tmp_path, now - timedelta(days=2))
    _, older = artifact(tmp_path, now - timedelta(days=3))
    _, other_source = artifact(
        tmp_path, now - timedelta(days=4), "00000000-0000-0000-0000-000000000002"
    )
    unrelated = tmp_path / "do-not-delete"
    unrelated.mkdir()
    partial = tmp_path / (".partial-" + newest)
    partial.mkdir()
    result = retain(replace(config, keep_count=1, max_age_days=1), now=now)
    assert set(result["preserved"]) == {newest, other_source} and result["removed"] == [older]
    assert unrelated.is_dir() and partial.is_dir()
    assert status(config)["last_successful_backup"] == newest


def test_age_boundary_and_count_limit_are_independent(tmp_path):
    now = datetime.now(UTC)
    config, newest = artifact(tmp_path, now)
    _, boundary = artifact(tmp_path, now - timedelta(days=1))
    _, excess = artifact(tmp_path, now - timedelta(hours=23))
    result = retain(replace(config, keep_count=3, max_age_days=1), now=now)
    assert set(result["preserved"]) == {newest, boundary, excess}
    result = retain(replace(config, keep_count=2, max_age_days=1), now=now)
    assert result["removed"] == [boundary]


def test_overlapping_process_is_refused_and_lock_releases_after_owner_exit(tmp_path):
    config, _ = fixture_artifact(tmp_path)
    script = """
import sys
from pathlib import Path
from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.locking import operation_lock
try:
    with operation_lock(BackupConfig('mysql+asyncmy://x:p@host/prl', Path(sys.argv[1]))):
        raise SystemExit(3)
except BackupError as error:
    raise SystemExit(0 if error.reason == Reason.BUSY else 4)
"""
    with operation_lock(config):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10
        )
        assert result.returncode == 0 and result.stderr == b""
    with operation_lock(config):
        pass
