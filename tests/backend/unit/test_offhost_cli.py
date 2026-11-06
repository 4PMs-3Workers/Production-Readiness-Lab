import json

import pytest
from tests.backend.unit.test_offhost_contract import configuration
from tools.backup import offhost, scheduled
from tools.backup.offhost_contract import RemoteError, canonical, contract


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


def test_import_rechecks_download_receipt_before_loading_or_rendering_images(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from tests.backend.unit.test_offhost_retrieve import uploaded
    from tools.backup.offhost_retrieve import retrieve

    _, target, remote, point = uploaded(tmp_path, monkeypatch)
    _, escrow_path = retrieve(target, remote, point.backup_id, tmp_path / "age", tmp_path / "key")
    calls = []
    monkeypatch.setattr(offhost, "import_images", lambda *args: calls.append(True))
    output = tmp_path / "recovered-images.yaml"
    args = SimpleNamespace(
        operation="import-images",
        local_store=target.store,
        backup_id=point.backup_id,
        output_overlay=output,
        output_configuration=tmp_path / "runtime-configuration",
    )
    result = offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert result["images_imported"] == 4 and result["recovery_overlay_written"] is True
    assert result["recovery_configuration_written"] is True
    assert (tmp_path / "runtime-configuration/tools/deploy/mysql/10-identities.sh").is_file()
    assert "pull_policy: never" in output.read_text()
    assert calls == [True]
    data = point.model_dump(mode="json")
    data["backup_manifest_sha256"] = "0" * 64
    (escrow_path / "POINT.json").write_bytes(canonical(data))
    with pytest.raises(RemoteError):
        offhost.perform(args, remote.config, tmp_path, datetime.now(UTC))
    assert calls == [True]
