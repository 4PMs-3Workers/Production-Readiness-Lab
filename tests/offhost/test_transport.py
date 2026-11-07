from dataclasses import replace
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from tests.backend.unit.test_backup_storage import fixture_artifact, populate
from tests.backend.unit.test_offhost_escrow import escrow_fixture
from tools.backup.offhost_contract import (
    Code,
    RemoteConfig,
    RemoteError,
    payload_key,
    point_key,
    source_identity,
)
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_lab import age_keys
from tools.backup.offhost_retrieve import retrieve
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import upload

from app.backup.storage import load_artifact, staging, write_private


def source(store_lab, age_executable, tmp_path):
    local_root, escrow = tmp_path / "source", tmp_path / "escrow"
    local_root.mkdir(mode=0o700)
    escrow.mkdir(mode=0o700)
    local, value = fixture_artifact(local_root)
    dependencies, _ = escrow_fixture(escrow)
    value["image_id"] = dependencies.images["backend"].local_id
    with staging(local, value["backup_id"]) as owned:
        populate(owned, value)
        manifest = owned.publish(value)
    identity, recipient = age_keys(age_executable, tmp_path / "independent-vault")
    config, credentials = store_lab.configure(
        source_identity(manifest), recipient, tmp_path / "credentials.json", request_seconds=2
    )
    return (
        local,
        S3(config, Deadline(120), credentials=credentials),
        manifest.backup_id,
        escrow,
        identity,
    )


def test_real_https_age_roundtrip_and_duplicate_with_source_quarantined(
    store_lab, age_executable, tmp_path
):
    local, remote, backup_id, escrow, identity = source(store_lab, age_executable, tmp_path)
    # Exercise the example policy on the server, independently of adapter scope checks.
    for action, key in (
        (remote.client.list_objects_v2, {"Prefix": "unrelated/"}),
        (remote.client.put_object, {"Key": "unrelated/denied", "Body": b"denied"}),
        (remote.client.delete_object, {"Key": point_key(remote.config, backup_id)}),
    ):
        with pytest.raises(ClientError) as denied:
            action(Bucket=remote.config.bucket, **key)
        assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    assert remote.get(point_key(remote.config, backup_id)) is None
    point = upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    assert upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work") == point
    response = store_lab.admin.get_object(
        Bucket=remote.config.bucket, Key=payload_key(remote.config, point)
    )
    with response["Body"] as handle:
        assert handle.read(32).startswith(b"age-encryption.org/v1")
    local.store.rename(tmp_path / "source-unavailable")
    target = replace(local, store=tmp_path / "recovered")
    retrieved, prepared = retrieve(target, remote, backup_id, age_executable, identity)
    assert retrieved == point and (prepared / "images/backend.tar").is_file()
    assert load_artifact(target, backup_id).image_id == point.backend_image_id


def test_lost_upload_acknowledgement_safely_restarts_without_overwriting(
    store_lab, age_executable, tmp_path, monkeypatch
):
    local, remote, backup_id, escrow, _ = source(store_lab, age_executable, tmp_path)
    original, lost = remote.client.put_object, []

    def put(**request):
        response = original(**request)
        if request["Key"].endswith(".age") and not lost:
            lost.append(True)
            raise EndpointConnectionError(endpoint_url="withheld")
        return response

    monkeypatch.setattr(remote.client, "put_object", put)
    point = upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    assert lost == [True]
    assert len([key for key in remote.inventory() if key.endswith("complete.json")]) == 1
    assert point.backup_id == backup_id


def test_failed_completion_is_not_visible_and_retry_keeps_local_success(
    store_lab, age_executable, tmp_path, monkeypatch
):
    local, remote, backup_id, escrow, _ = source(store_lab, age_executable, tmp_path)
    original = remote.put
    monkeypatch.setattr(
        remote, "put", lambda *args: (_ for _ in ()).throw(RemoteError(Code.TRANSPORT))
    )
    with pytest.raises(RemoteError):
        upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    assert remote.get(point_key(remote.config, backup_id)) is None
    assert load_artifact(local, backup_id).backup_id == backup_id
    monkeypatch.setattr(remote, "put", original)
    assert (
        upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work").backup_id
        == backup_id
    )


def test_wrong_identity_key_and_corrupt_remote_payload_never_fall_back(
    store_lab, age_executable, tmp_path
):
    local, remote, backup_id, escrow, identity = source(store_lab, age_executable, tmp_path)
    point = upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    target = replace(local, store=tmp_path / "recovery")
    wrong, _ = age_keys(age_executable, tmp_path / "wrong-vault")
    with pytest.raises(RemoteError):
        retrieve(target, remote, backup_id, age_executable, wrong)
    assert not (target.store / backup_id).exists()
    with pytest.raises(RemoteError):
        retrieve(target, remote, backup_id, age_executable, tmp_path / "missing-key")
    store_lab.admin.delete_object(
        Bucket=remote.config.bucket, Key=payload_key(remote.config, point)
    )
    with pytest.raises(RemoteError):
        retrieve(target, remote, backup_id, age_executable, identity)
    store_lab.admin.put_object(
        Bucket=remote.config.bucket, Key=payload_key(remote.config, point), Body=b"corrupt"
    )
    with pytest.raises(RemoteError):
        retrieve(target, remote, backup_id, age_executable, identity)
    assert not (target.store / backup_id).exists()
    assert load_artifact(local, backup_id).backup_id == backup_id


def test_actual_store_retention_preserves_last_point_and_unrelated_prefix(
    store_lab, age_executable, tmp_path
):
    from tools.backup.offhost_retention import retain

    local, remote, backup_id, escrow, _ = source(store_lab, age_executable, tmp_path)
    point = upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    unrelated = "unrelated/keep-" + point.encrypted_sha256
    store_lab.admin.put_object(Bucket=remote.config.bucket, Key=unrelated, Body=b"keep")
    assert retain(remote, tmp_path / "work")["removed_count"] == 0
    assert remote.get(point_key(remote.config, backup_id)) is not None
    response = store_lab.admin.get_object(Bucket=remote.config.bucket, Key=unrelated)
    with response["Body"] as handle:
        assert handle.read() == b"keep"


def test_store_conditional_retirement_and_pruning_keep_the_last_verified_point(
    store_lab, age_executable, tmp_path
):
    from tools.backup.offhost_retention import inventory, retain
    from tools.backup.offhost_upload import read_point

    local, remote, backup_id, escrow, _ = source(store_lab, age_executable, tmp_path)
    values = [upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")]
    initial = load_artifact(local, backup_id)
    for _ in range(2):
        value = initial.model_dump(mode="json")
        value["backup_id"] = "prl-backup-20261028T120000Z-" + uuid4().hex
        with staging(local, value["backup_id"]) as owned:
            for path in (local.store / backup_id).glob("*.jsonl"):
                write_private(owned.path / path.name, path.read_bytes())
            manifest = owned.publish(value)
        values.append(
            upload(local, remote, manifest.backup_id, escrow, age_executable, tmp_path / "work")
        )
    key = point_key(remote.config, values[0].backup_id)
    with pytest.raises(RemoteError, match=Code.CONFLICT.value):
        remote.replace(key, b"must-not-replace", '"incorrect-etag"')
    remote.config = RemoteConfig.model_validate(dict(remote.config.model_dump(), keep_count=1))
    assert retain(remote, tmp_path / "work")["removed_count"] == 2
    remaining, _ = inventory(remote)
    assert [point.backup_id for point, _ in remaining] == [values[-1].backup_id]
    assert retain(remote, tmp_path / "work")["removed_count"] == 0
    with pytest.raises(RemoteError, match=Code.CONFLICT.value):
        read_point(remote, values[0].backup_id)
    assert load_artifact(local, backup_id).backup_id == backup_id


def test_invalid_source_identity_is_refused_before_remote_publication(
    store_lab, age_executable, tmp_path
):
    local, remote, backup_id, escrow, _ = source(store_lab, age_executable, tmp_path)
    original_scope = remote.config.scope
    remote.config = RemoteConfig.model_validate(
        dict(remote.config.model_dump(), source_id="0" * 64)
    )
    with pytest.raises(RemoteError, match=Code.IDENTITY.value):
        upload(local, remote, backup_id, escrow, age_executable, tmp_path / "work")
    for scope in (original_scope, remote.config.scope):
        assert not store_lab.admin.list_objects_v2(
            Bucket=remote.config.bucket, Prefix=scope + "points/"
        ).get("Contents")
