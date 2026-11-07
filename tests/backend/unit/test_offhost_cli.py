import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.backend.unit.test_offhost_contract import configuration
from tests.backend.unit.test_offhost_escrow import image_archive
from tests.backend.unit.test_offhost_upload import inputs
from tools.backup import offhost, scheduled
from tools.backup.offhost_contract import Code, RemoteError, canonical, contract, point_key
from tools.backup.offhost_escrow import Escrow, release_at, verify
from tools.backup.offhost_retrieve import retrieve
from tools.backup.offhost_upload import upload


def test_invalid_configuration_prints_only_a_finite_failure(capsys):
    assert offhost.main(["upload", "--config", "missing-private-secret"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["success"] is False and report["reason"] == "REMOTE_CONFIGURATION_INVALID"
    assert "missing-private-secret" not in json.dumps(report)


def test_public_reports_cannot_carry_raw_payloads_or_claim_application_success():
    base = dict(operation="upload", success=True, duration_seconds=1.0)
    for field, value in (
        ("credentials", "private"),
        ("raw_error", "sensitive"),
        ("application_rehearsal_verified", True),
    ):
        with pytest.raises(RemoteError):
            contract(canonical(dict(base, **{field: value})), offhost.Report)


def test_missing_remote_credentials_fail_closed_in_a_supervised_worker(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_bytes(canonical(configuration()))
    config.chmod(0o600)
    assert (
        offhost.main(["status", "--config", str(config), "--local-store", str(tmp_path / "store")])
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == "REMOTE_KEY_UNAVAILABLE"
    assert report["application_rehearsal_verified"] is False
    assert not list((tmp_path / "store/.offhost-jobs").glob("job-*"))


@pytest.mark.parametrize("local_result,remote_result", [(0, 0), (0, 1), (1, 0)])
def test_scheduled_local_and_remote_outcomes_are_independent(
    tmp_path, monkeypatch, capsys, local_result, remote_result
):
    calls = []
    backup_id = "prl-backup-20261001T000000Z-" + "a" * 32

    def local():
        print(json.dumps(dict(operation="backup", success=True, backup_id=backup_id)))
        print(json.dumps(dict(operation="scheduled", success=local_result == 0)))
        return local_result

    def remote(args):
        calls.append(args)
        return remote_result

    monkeypatch.setenv("PRL_OFFHOST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(scheduled.run, "main", local)
    monkeypatch.setattr(scheduled, "bind_store", lambda env: tmp_path)
    monkeypatch.setattr(scheduled.offhost, "main", remote)
    assert scheduled.main() == (local_result or remote_result)
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[0]["success"] is True
    assert reports[1]["success"] is (local_result == 0)
    assert calls[0] == ["upload", "--backup-id", backup_id]
    assert len(calls) == (2 if remote_result == 0 else 1)


def test_scheduled_binding_cannot_read_an_unrelated_host_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        scheduled.run,
        "inspected",
        lambda *args: dict(
            Labels={"prl.backup.storage": "1"},
            Options=dict(o="bind", device=str(tmp_path / "different")),
        ),
    )
    with pytest.raises(RemoteError):
        scheduled.bind_store(
            dict(PRL_OFFHOST_LOCAL_STORE=str(tmp_path / "expected"), PRL_BACKUP_VOLUME="owned")
        )


def test_scheduled_invalid_binding_aborts_before_snapshot_and_retention(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PRL_OFFHOST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PRL_OFFHOST_LOCAL_STORE", str(tmp_path / "expected"))
    monkeypatch.setenv("PRL_BACKUP_VOLUME", "owned")
    monkeypatch.setattr(
        scheduled.run,
        "inspected",
        lambda *args: dict(
            Labels={"prl.backup.storage": "1"},
            Options=dict(o="bind", device=str(tmp_path / "unrelated")),
        ),
    )
    calls = []
    monkeypatch.setattr(scheduled.run, "main", lambda: calls.append("local"))
    monkeypatch.setattr(scheduled.offhost, "main", lambda args: calls.append("remote"))
    assert scheduled.main() == 1
    assert calls == []
    assert json.loads(capsys.readouterr().out)["reason"] == "REMOTE_CONFIGURATION_INVALID"


def test_import_rechecks_download_receipt_before_loading_or_rendering_images(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.backend.unit.test_offhost_retrieve import uploaded

    _, target, remote, point = uploaded(tmp_path, monkeypatch)
    _, escrow_path = retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    calls = []
    monkeypatch.setattr("tools.backup.offhost_escrow.run", lambda *args: calls.append(True))
    monkeypatch.setattr(
        "tools.backup.offhost_escrow.inspect",
        lambda reference, deadline: dict(id=reference, os="linux", arch="amd64"),
    )
    output = tmp_path / "recovered-images.yaml"
    args = SimpleNamespace(
        operation="import-images",
        local_store=target.store,
        backup_id=point.backup_id,
        output_overlay=output,
        output_configuration=tmp_path / "runtime-configuration",
        use_installed_vendors=False,
    )
    monkeypatch.setattr(
        "app.backup.manifest.migration_contract", lambda: ("future_revision", "f" * 64)
    )
    result = offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert result["images_imported"] == 4 and result["recovery_overlay_written"] is True
    assert result["recovery_configuration_written"] is True
    assert (tmp_path / "runtime-configuration/tools/deploy/mysql/10-identities.sh").is_file()
    assert "pull_policy: never" in output.read_text()
    assert len(result["imported_image_ids"]) == 4
    assert len(calls) == 4
    data = point.model_dump(mode="json")
    data["backup_manifest_sha256"] = "0" * 64
    (escrow_path / "POINT.json").write_bytes(canonical(data))
    with pytest.raises(RemoteError):
        offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert len(calls) == 4


def test_v1_restore_import_uses_authenticated_installed_vendors_only(tmp_path, monkeypatch):
    local, remote, backup_id, source = inputs(tmp_path, monkeypatch)
    release = release_at(source / "release.json")
    source_record = source / "escrow.json"
    escrow = contract(source_record.read_bytes(), Escrow)
    vendor = source / "images/mysql.tar"
    installed = tmp_path / "independently-installed.tar"
    shutil.copyfile(vendor, installed)
    installed_id = escrow.images["mysql"].config_id
    proof = json.loads((source / "images/mysql.pin.json").read_bytes())
    for path in (source / "images").glob("*.pin.json"):
        path.unlink()

    # Emulate a v1 archive that claimed the pinned vendor name but held other bytes.
    substituted = image_archive(vendor, source="b" * 40)
    payload = vendor.read_bytes()
    escrow.images["mysql"] = escrow.images["mysql"].model_copy(
        update=dict(
            local_id=substituted,
            config_id=substituted,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
        )
    )
    source_record.write_bytes(canonical(escrow))
    monkeypatch.setattr(
        "tools.backup.offhost_upload.verify",
        lambda *args: verify(*args, require_vendor_pin=False),
    )
    point = upload(local, remote, backup_id, source, tmp_path / "age", tmp_path / "work")
    legacy = point.model_copy(update={"format_version": 1})
    remote.client.objects[point_key(remote.config, backup_id)] = canonical(legacy)
    target = replace(local, store=tmp_path / "recovery")

    def decrypt(executable, source, destination, deadline, **kwargs):
        shutil.copyfile(source, destination)
        destination.chmod(0o600)

    monkeypatch.setattr("tools.backup.offhost_retrieve.age", decrypt)
    _, downloaded = retrieve(target, remote, backup_id, tmp_path / "age", tmp_path / "key")
    loaded = []
    vendor_refs = set(release.vendor_images.values())

    def docker(args, deadline):
        if args[:3] == ["docker", "image", "save"]:
            assert args[-1] == installed_id
            shutil.copyfile(installed, args[4])
        else:
            assert args[:3] == ["docker", "image", "load"]
            loaded.append(Path(args[-1]).stem)

    def registry(args, deadline):
        reference = args[-1].removeprefix("docker://")
        assert reference in vendor_refs
        return proof[reference.rsplit("@", 1)[1]].encode()

    monkeypatch.setattr("tools.backup.offhost_escrow.run", docker)
    monkeypatch.setattr("tools.backup.offhost_escrow.capture", registry)
    monkeypatch.setattr(
        "tools.backup.offhost_escrow.inspect",
        lambda reference, _: dict(
            id=installed_id if reference in vendor_refs else reference, os="linux", arch="amd64"
        ),
    )
    overlay, runtime = tmp_path / "images.yaml", tmp_path / "runtime"
    args = offhost.arguments(
        [
            "import-images",
            "--config",
            str(tmp_path / "config.json"),
            "--local-store",
            str(target.store),
            "--backup-id",
            backup_id,
            "--output-overlay",
            str(overlay),
            "--output-configuration",
            str(runtime),
            "--use-installed-vendors",
        ]
    )
    args.use_installed_vendors = False
    with pytest.raises(RemoteError, match=Code.IMAGE.value):
        offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    args.use_installed_vendors = True
    assert not loaded and not overlay.exists() and not runtime.exists()
    bad = tmp_path / "wrong-installed.tar"
    image_archive(bad, source="c" * 40)
    good = installed.read_bytes()
    shutil.copyfile(bad, installed)
    with pytest.raises(RemoteError, match=Code.IMAGE.value):
        offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert not loaded and not overlay.exists() and not runtime.exists()

    installed.write_bytes(good)
    result = offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert set(loaded) == set(release.images)
    assert result["images_imported"] == len(release.images)
    assert result["vendor_images_reused"] == len(vendor_refs)
    assert result["imported_image_ids"]["mysql"] == installed_id
    assert substituted not in overlay.read_text()
    assert "pull_policy: never" in overlay.read_text()
    assert (runtime / "tools/deploy/compose.yaml").is_file()

    (downloaded / "POINT.json").write_bytes(canonical(point))
    with pytest.raises(RemoteError, match=Code.CONFIGURATION.value):
        offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert set(loaded) == set(release.images)
