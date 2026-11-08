import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.release_manifest import ReleaseError
from tools.deploy.rollout_lock import operation_lock
from tools.deploy.rollout_state import RolloutError


def test_concurrent_controller_rollback_does_not_mutate_the_active_deployment(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    with operation_lock(host.store):
        other = controller(tmp_path)
        with pytest.raises(RolloutError, match="operation-locked"):
            other.rollback()
    assert host.store.load() == before


def test_incompatible_configuration_refuses_before_any_service_mutation(tmp_path, monkeypatch):
    from tools.deploy import rollout_preflight

    host = controller(tmp_path)
    before = host.deploy(reference())
    host.registry.releases[reference(2)] = release(124, reference())
    host.runtime.calls.clear()

    def incompatible(spec, root, *, allow_previous):
        assert not allow_previous
        raise ReleaseError("incompatible-configuration")

    monkeypatch.setattr(rollout_preflight, "require_compatible", incompatible)
    refused = host.deploy(reference(2))
    assert refused.operation is not None
    assert refused.operation.events[-1].reason == "incompatible-configuration"
    assert refused.stable == before.stable
    assert host.runtime.calls == []


def test_missing_manifest_records_refusal_and_preserves_stable(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    refused = host.deploy(reference(99))
    assert refused.refusal is not None and refused.refusal.reason == "invalid-manifest"
    assert refused.stable == before.stable


def test_deadline_failure_during_rollback_never_overwrites_stable(tmp_path):
    from tests.backend.unit.test_rollout_rollback import two_releases

    host, before = two_releases(tmp_path)

    def timeout(runtime):
        raise RolloutError("deadline-exceeded")

    host.control.readiness = timeout
    result = host.rollback()
    assert result.operation is not None and result.operation.phase == "failed"
    assert result.operation.events[-1].reason == "deadline-exceeded"
    assert result.stable == before.stable and result.previous_stable == before.previous_stable
