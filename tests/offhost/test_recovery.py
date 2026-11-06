import os
import shutil
import sys

import pytest
from tools.backup.evidence import Evidence, write_report
from tools.backup.offhost_contract import canonical
from tools.backup.offhost_rehearsal import rehearse
from tools.deploy.release_manifest import ROOT


def test_remote_only_recovered_application_with_source_and_registry_unavailable(
    store_lab, age_executable, tmp_path
):
    if sys.platform != "linux" or any(
        shutil.which(tool) is None for tool in ("docker", "skopeo", "oras", "git")
    ):
        pytest.fail(
            "Required off-host app recovery needs Linux/Docker and immutable-release tools; "
            "cannot skip"
        )
    service, point, checks, configuration_sha256 = rehearse(
        tmp_path / "private", store_lab, age_executable
    )
    proof = Evidence.model_validate(service)
    assert all(checks.values()) and proof.source_unchanged
    assert (
        proof.source_sha == point.source_sha
        and proof.backup.manifest_sha256 == point.backup_manifest_sha256
    )
    destination = ROOT / "build/offhost-review"
    destination.mkdir(parents=True, exist_ok=True)
    write_report(service, destination / "service.json")
    (destination / "point.json").write_bytes(canonical(point))
    (destination / "isolation.json").write_text(__import__("json").dumps(checks), encoding="utf-8")
    assert len(configuration_sha256) == 64
    assert os.environ.get("RUN_OFFHOST_TESTS") == "1"
