import pytest
from tests.backend.unit.test_rollout_state import operation
from tools.deploy.release_manifest import ReleaseError, canonical, parse
from tools.deploy.rollout_state import Operation


def failed_preflight(*, command="deploy", pending=False):
    data = operation(("preflight",)).model_dump()
    data.update(command=command, recovery_pending=pending)
    data["events"][0]["phase"] = "preflight" if command == "deploy" else "rollback_preflight"
    data["events"].append(
        dict(
            phase="failed",
            at="2026-11-05T00:00:01Z",
            elapsed_seconds=1.0,
            reason="missing-images",
        )
    )
    return parse(canonical(data), Operation)


def test_resume_preflight_failure_retains_unresolved_recovery_intent():
    assert failed_preflight(pending=True).requires_recovery
    assert not failed_preflight().requires_recovery


def test_legacy_failed_rollback_preflight_already_identifies_its_recovery_target():
    data = failed_preflight(command="rollback").model_dump()
    data.pop("recovery_pending")
    restored = parse(canonical(data), Operation)
    assert restored.requires_recovery
    assert restored.target == failed_preflight(command="rollback").target


def test_complete_operation_cannot_claim_unresolved_recovery():
    data = operation().model_dump()
    data["recovery_pending"] = True
    with pytest.raises(ReleaseError):
        parse(canonical(data), Operation)
