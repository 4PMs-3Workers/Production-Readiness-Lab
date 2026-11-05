import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.backend.unit.test_backup_manifest import sample_manifest

from app.backup.codec import encode_row
from app.backup.config import BackupConfig, BackupError
from app.backup.storage import load_artifact, staging, write_private


@pytest.mark.parametrize("budgeted", [False, True])
def test_large_artifact_verification_bounds_underlying_read_calls(tmp_path, monkeypatch, budgeted):
    from app.backup import storage

    config, value = fixture_artifact(tmp_path)
    table = next(table for table in value["source"]["tables"] if table["name"] == "users")
    payload = encode_row(["x" * 2048, *([None] * (len(table["columns"]) - 1))]) * 640
    value["files"]["users"] = dict(
        rows=640, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()
    )
    with staging(config, value["backup_id"]) as owned:
        populate(owned, value)
        (owned.path / "users.jsonl").write_bytes(payload)
        owned.publish(value)
    total_bytes = sum(path.stat().st_size for path in (tmp_path / value["backup_id"]).iterdir())
    budget = storage.ByteBudget(total_bytes) if budgeted else None
    read_calls = 0

    class CountedFile(io.FileIO):
        def count(self):
            nonlocal read_calls
            read_calls += 1
            assert read_calls < 512, "artifact verification must not read raw streams byte by byte"

        def read(self, size: int | None = -1):
            self.count()
            return super().read(size)

        def readinto(self, buffer):
            self.count()
            return super().readinto(buffer)

    def counted_fdopen(descriptor, mode, buffering=-1):
        raw = CountedFile(descriptor, mode)
        return (
            raw if buffering == 0 else io.BufferedReader(raw, buffering if buffering > 0 else 8192)
        )

    monkeypatch.setattr(storage.os, "fdopen", counted_fdopen)
    verified = load_artifact(config, value["backup_id"], byte_budget=budget)

    assert verified.files["users"].bytes > 1_000_000
    assert verified.files["users"].rows == 640
    if budget is not None:
        assert budget.remaining == 0


def fixture_artifact(tmp_path):
    config = BackupConfig("mysql+asyncmy://x:private@host/prl", tmp_path, min_free_bytes=0)
    value = sample_manifest()
    value["started_at"] = (
        (datetime.now(UTC) - timedelta(seconds=1))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    value["completed_at"] = (
        datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
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


def test_completion_timestamp_includes_artifact_verification(tmp_path, monkeypatch):
    from app.backup import storage
    from app.backup.manifest import timestamp

    config, value = fixture_artifact(tmp_path)
    original = storage._verify
    validated = []

    def checked(*args, **kwargs):
        result = original(*args, **kwargs)
        validated.append(datetime.now(UTC))
        return result

    monkeypatch.setattr(storage, "_verify", checked)
    with staging(config, value["backup_id"]) as owned:
        populate(owned, value)
        manifest = owned.publish(value)
    assert timestamp(manifest.completed_at) >= validated[0]
