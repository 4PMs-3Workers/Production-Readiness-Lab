import os
import shutil
import sys

import pytest
from tools.backup.offhost_evidence import evidence, write_report
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
    proof = evidence(service, point, checks, configuration_sha256)
    assert proof.evidence_version == 3 and proof.engine_images is not None
    assert all(checks.values()) and proof.source_unchanged
    assert (
        proof.source_sha == point.source_sha
        and proof.backup.manifest_sha256 == point.backup_manifest_sha256
    )
    destination = ROOT / "build/offhost-review"
    destination.mkdir(parents=True, exist_ok=True)
    write_report(proof, destination / "rehearsal.json")
    assert os.environ.get("RUN_OFFHOST_TESTS") == "1"
