import hashlib
from dataclasses import replace

import pytest
from tests.backend.unit.test_backup_manifest import sample_manifest

from app.backup.codec import encode_row
from app.backup.config import BackupConfig, BackupError
from app.backup.storage import load_artifact, staging, write_private


def fixture_artifact(tmp_path):
    config = BackupConfig("mysql+asyncmy://x:private@host/prl", tmp_path, min_free_bytes=0)
    value = sample_manifest()
    for table in value["source"]["tables"]:
        row = (
            [value["source"]["revision"]]
            if table["name"] == "alembic_version"
            else [None] * len(table["columns"])
        )
        raw = encode_row(row)
        value["files"][table["name"]] = dict(
            rows=1, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
        )
    return config, value


def populate(owned, value):
    for table in value["source"]["tables"]:
        row = (
            [value["source"]["revision"]]
            if table["name"] == "alembic_version"
            else [None] * len(table["columns"])
        )
        write_private(owned.path / (table["name"] + ".jsonl"), encode_row(row))


def test_only_atomically_published_complete_artifact_is_accepted(tmp_path):
    config, value = fixture_artifact(tmp_path)
    with staging(config, value["backup_id"]) as owned:
        populate(owned, value)
        with pytest.raises(BackupError):
            load_artifact(config, value["backup_id"])
        owned.publish(value)
    assert load_artifact(config, value["backup_id"]).backup_id == value["backup_id"]
    assert not list(tmp_path.glob(".partial-*"))


@pytest.mark.parametrize("fault", ["truncate", "missing", "marker", "extra"])
def test_integrity_failures_are_rejected_before_restore(tmp_path, fault):
    config, value = fixture_artifact(tmp_path)
    with staging(config, value["backup_id"]) as owned:
        populate(owned, value)
        owned.publish(value)
    artifact = tmp_path / value["backup_id"]
    if fault == "truncate":
        (artifact / "users.jsonl").write_bytes(b"[")
    elif fault == "missing":
        (artifact / "manifest.json").unlink()
    elif fault == "marker":
        (artifact / "COMPLETE").write_bytes(b"0" * 64)
    else:
        (artifact / "unrelated").mkdir()
    with pytest.raises(BackupError):
        load_artifact(config, value["backup_id"])


def test_failed_or_overbudget_staging_never_appears_complete_and_keeps_other_paths(tmp_path):
    config, value = fixture_artifact(tmp_path)
    other = tmp_path / "keep-unrelated"
    other.mkdir()
    with pytest.raises(BackupError):
        with staging(replace(config, max_bytes=1024), value["backup_id"]) as owned:
            populate(owned, value)
            owned.publish(value)
    assert other.is_dir() and not (tmp_path / value["backup_id"]).exists()
    assert not list(tmp_path.glob(".partial-*"))
