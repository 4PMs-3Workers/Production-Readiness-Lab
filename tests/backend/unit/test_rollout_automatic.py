import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.rollout_state import RolloutError


@pytest.mark.parametrize("failure", ["smoke", "rollout", "readiness"])
def test_failed_release_automatically_restores_compatible_stable(tmp_path, failure):
    host = controller(tmp_path)
    before = host.deploy(reference())
    target = release(124, reference())
    host.registry.releases[reference(2)] = target
    host.runtime.previous_stable = before.stable
    host.runtime.fail, host.runtime.fail_release = failure, target.release_id
    if failure == "readiness":

        def readiness(runtime):
            if runtime.manifest.release_id == target.release_id:
                raise RolloutError("readiness-failed")

        host.control.readiness = readiness
    result = host.deploy(reference(2))
    assert result.operation is not None and result.operation.phase == "rolled_back"
    assert result.operation.trigger_reason == failure + "-failed"
    assert result.operation.target.reference == reference(2)
    assert result.operation.fallback == before.stable
    assert result.stable == before.stable and result.previous_stable == before.previous_stable
    decided = next(e for e in result.operation.events if e.phase == "rollback_preflight")
    assert result.operation.events[-1].elapsed_seconds - decided.elapsed_seconds <= 300


def test_rollback_failure_is_not_hidden_by_retained_stable_pointer(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    host.runtime.previous_stable = before.stable
    host.registry.releases[reference(2)] = release(124, reference())
    host.runtime.fail = "smoke"  # Fails on both the candidate and the restored image.
    result = host.deploy(reference(2))
    assert result.operation is not None and result.operation.phase == "failed"
    assert result.operation.trigger_reason == "smoke-failed"
    assert result.operation.events[-1].reason == "smoke-failed"
    assert result.stable == before.stable and result.previous_stable == before.previous_stable
    assert host.store.load() == result


def test_failed_first_deployment_cannot_invent_a_rollback_target(tmp_path):
    host = controller(tmp_path)
    host.runtime.fail = "smoke"
    result = host.deploy(reference())
    assert result.operation is not None and result.operation.phase == "failed"
    assert result.operation.fallback is None and result.stable is None
    assert not any(e.phase == "rollback_preflight" for e in result.operation.events)
