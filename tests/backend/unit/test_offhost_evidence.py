import copy

import pytest
from tests.backend.unit.test_backup_evidence import report
from tools.backup.evidence import Evidence
from tools.backup.offhost_contract import Completion
from tools.backup.offhost_evidence import Isolation, OffhostEvidence, evidence


def sample():
    service = report()
    backup = service["backup"]
    point = Completion.model_validate(
        dict(
            backup_id=backup["backup_id"],
            source_id="a" * 64,
            configuration_sha256="b" * 64,
            source_sha=service["source_sha"],
            backend_image_id=backup["image_id"],
            backup_manifest_sha256=backup["manifest_sha256"],
            release_sha256="d" * 64,
            bundle_sha256="e" * 64,
            encrypted_sha256="f" * 64,
            encrypted_bytes=1024,
            snapshot=backup["snapshot"],
            backup_completed_at=backup["completed_at"],
            verified_at="2026-10-28T12:00:01.000000Z",
        )
    )
    checks = {key: True for key in Isolation.model_fields}
    return evidence(service, point, checks, "c" * 64)


def test_local_history_and_remote_acceptance_have_distinct_versions_and_commands():
    local, remote = Evidence.model_validate(report()), sample()
    assert local.evidence_version == 1 and remote.evidence_version == 2
    with pytest.raises(ValueError):
        OffhostEvidence.model_validate(local.model_dump())
    with pytest.raises(ValueError):
        Evidence.model_validate(remote.model_dump())
    assert remote.timing.rto_service_seconds == 10.0


@pytest.mark.parametrize(
    "fault", ["identity", "point", "isolation", "payload", "timing", "commands"]
)
def test_incomplete_mismatched_or_sensitive_proof_is_refused(fault):
    data = copy.deepcopy(sample().model_dump(mode="json"))
    if fault == "identity":
        data["recovery_point"]["source_sha"] = "c" * 40
    elif fault == "point":
        data["recovery_point"]["backup_manifest_sha256"] = "1" * 64
    elif fault == "isolation":
        data["isolation"]["source_services_stopped"] = False
    elif fault == "payload":
        data["recovery_point"]["credentials"] = "never-publish"
    elif fault == "timing":
        data["timing"]["rpo_observed_max_seconds"] = 0.0
    else:
        data["commands"] = ["arbitrary command with private payload"]
    with pytest.raises(ValueError):
        OffhostEvidence.model_validate(data)
