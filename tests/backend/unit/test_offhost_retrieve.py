import shutil
from dataclasses import replace

import pytest
from tests.backend.unit.test_offhost_upload import inputs
from tools.backup.offhost_contract import Code, RemoteError, payload_key, point_key
from tools.backup.offhost_retrieve import retrieve
from tools.backup.offhost_upload import upload

from app.backup.config import BackupError, Reason
from app.backup.storage import load_artifact


def uploaded(tmp_path, monkeypatch):
    local, remote, backup_id, escrow = inputs(tmp_path, monkeypatch)
    point = upload(local, remote, backup_id, escrow, tmp_path / "age", tmp_path / "work")
    target = replace(local, store=tmp_path / "recovery")

    def fake_age(executable, source, destination, deadline, **kwargs):
        shutil.copyfile(source, destination)
        destination.chmod(0o600)

    monkeypatch.setattr("tools.backup.offhost_retrieve.age", fake_age)
    return local, target, remote, point


def test_retrieval_uses_only_remote_input_and_original_restore_verification(tmp_path, monkeypatch):
    local, target, remote, point = uploaded(tmp_path, monkeypatch)
    # Quarantine the source store before retrieval; the target has no fallback path.
    local.store.rename(tmp_path / "unavailable-source")
    result, escrow = retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    assert result == point
    assert (escrow / "images/backend.tar").is_file()
    assert (escrow / "configuration/tools/deploy/compose.yaml").is_file()
    assert load_artifact(target, point.backup_id).image_id == point.backend_image_id
    assert retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")[0] == point


def test_retrieval_survives_new_migration_checkout_but_runtime_check_stays_strict(
    tmp_path, monkeypatch
):
    _, target, remote, point = uploaded(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.backup.manifest.migration_contract", lambda: ("future_revision", "f" * 64)
    )
    for _ in range(2):
        assert (
            retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")[0]
            == point
        )
    with pytest.raises(BackupError) as error:
        load_artifact(target, point.backup_id)
    assert error.value.reason is Reason.INCOMPATIBLE


@pytest.mark.parametrize("fault", ["missing-point", "missing-payload", "corrupt", "missing-key"])
def test_remote_failure_never_falls_back_to_good_source_or_publishes_target(
    tmp_path, monkeypatch, fault
):
    local, target, remote, point = uploaded(tmp_path, monkeypatch)
    if fault == "missing-point":
        remote.client.objects.pop(point_key(remote.config, point.backup_id))
    elif fault == "missing-payload":
        remote.client.objects.pop(payload_key(remote.config, point))
    elif fault == "corrupt":
        remote.client.objects[payload_key(remote.config, point)] = b"corrupt"
    else:
        monkeypatch.setattr(
            "tools.backup.offhost_retrieve.age",
            lambda *args, **kwargs: (_ for _ in ()).throw(RemoteError(Code.KEY)),
        )
    with pytest.raises(RemoteError):
        retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    assert not (target.store / point.backup_id).exists()
    assert load_artifact(local, point.backup_id).backup_id == point.backup_id
    assert not list(target.store.glob(".partial-offhost-*"))


def test_failed_authoritative_publish_can_retry_prepared_escrow(tmp_path, monkeypatch):
    _, target, remote, point = uploaded(tmp_path, monkeypatch)
    import os

    original = os.rename
    failed = []

    def fail_once(source, destination):
        if destination == target.store / point.backup_id and not failed:
            failed.append(True)
            raise OSError("injected atomic replacement failure")
        return original(source, destination)

    monkeypatch.setattr("tools.backup.offhost_retrieve.os.rename", fail_once)
    with pytest.raises(OSError):
        retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    assert not (target.store / point.backup_id).exists()
    result, _ = retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    assert result == point and failed == [True]
