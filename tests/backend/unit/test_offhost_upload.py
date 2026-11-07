import shutil

import pytest
from tests.backend.unit.offhost_fixtures import Store
from tests.backend.unit.test_backup_storage import fixture_artifact, populate
from tests.backend.unit.test_offhost_contract import configuration
from tests.backend.unit.test_offhost_escrow import escrow_fixture
from tools.backup.offhost_contract import (
    Code,
    RemoteConfig,
    RemoteError,
    point_key,
    source_identity,
)
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import upload

from app.backup.config import BackupError
from app.backup.storage import load_artifact, staging


def inputs(tmp_path, monkeypatch):
    root, escrow = tmp_path / "local", tmp_path / "escrow"
    root.mkdir(mode=0o700)
    escrow.mkdir(mode=0o700)
    local, value = fixture_artifact(root)
    _, backup = escrow_fixture(escrow)
    value["image_id"] = backup.image_id
    with staging(local, value["backup_id"]) as owned:
        populate(owned, value)
        manifest = owned.publish(value)
    remote_config = RemoteConfig.model_validate(
        dict(configuration().model_dump(), source_id=source_identity(manifest))
    )
    remote = S3(remote_config, Deadline(20), client=Store())

    # This double tests publication ordering, not encryption. Real age is required in acceptance.
    def fake_age(executable, source, target, deadline, **kwargs):
        shutil.copyfile(source, target)
        target.chmod(0o600)

    monkeypatch.setattr("tools.backup.offhost_upload.age", fake_age)
    return local, remote, manifest.backup_id, escrow


def test_completion_requires_verified_inputs_and_remote_readback_and_is_duplicate_safe(
    tmp_path, monkeypatch
):
    local, remote, backup_id, escrow = inputs(tmp_path, monkeypatch)
    point = upload(local, remote, backup_id, escrow, tmp_path / "age", tmp_path / "work")
    before = dict(remote.client.objects)
    assert upload(local, remote, backup_id, escrow, tmp_path / "age", tmp_path / "work") == point
    assert before == remote.client.objects
    assert remote.client.calls[-1][0] == "get"
    assert load_artifact(local, backup_id).backup_id == backup_id


@pytest.mark.parametrize(
    "fault", ["input", "identity", "missing-image", "upload", "checksum", "completion"]
)
def test_failed_transfer_never_marks_complete_or_deletes_good_local_copy(
    tmp_path, monkeypatch, fault
):
    local, remote, backup_id, escrow = inputs(tmp_path, monkeypatch)
    if fault == "input":
        (local.store / backup_id / "users.jsonl").write_bytes(b"invalid")
    elif fault == "identity":
        remote.config = RemoteConfig.model_validate(
            dict(remote.config.model_dump(), source_id="b" * 64)
        )
    elif fault == "missing-image":
        (escrow / "images/backend.tar").unlink()
    elif fault == "upload":
        monkeypatch.setattr(
            remote, "upload", lambda *args: (_ for _ in ()).throw(RemoteError(Code.TRANSPORT))
        )
    elif fault == "checksum":
        original = remote.client.put_object

        def corrupt(**request):
            result = original(**request)
            if request["Key"].endswith(".age"):
                remote.client.objects[request["Key"]] = b"corrupt"
            return result

        monkeypatch.setattr(remote.client, "put_object", corrupt)
    else:
        monkeypatch.setattr(
            remote, "put", lambda *args: (_ for _ in ()).throw(RemoteError(Code.TRANSPORT))
        )
    with pytest.raises((RemoteError, BackupError, OSError)):
        upload(local, remote, backup_id, escrow, tmp_path / "age", tmp_path / "work")
    assert point_key(remote.config, backup_id) not in remote.client.objects
    assert (local.store / backup_id / "COMPLETE").exists()
    if fault != "input":
        assert load_artifact(local, backup_id).backup_id == backup_id


@pytest.mark.parametrize("name", ["backup/users.jsonl", "images/backend.tar", "escrow.json"])
def test_mutation_after_validation_cannot_publish_completion(tmp_path, monkeypatch, name):
    from tools.backup.offhost_package import pack

    local, remote, backup_id, escrow = inputs(tmp_path, monkeypatch)

    def change_before_pack(files, *args):
        path, entry = files[name]
        path.write_bytes(b"x" * entry.bytes)
        return pack(files, *args)

    monkeypatch.setattr("tools.backup.offhost_upload.pack", change_before_pack)
    with pytest.raises(RemoteError, match=Code.INTEGRITY.value):
        upload(local, remote, backup_id, escrow, tmp_path / "age", tmp_path / "work")
    assert remote.client.objects == {}
