import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tests.backend.unit.test_rollout_interruption import SimulatedCrash
from tools.deploy.rollout_controller import RolloutController


def interrupted_deploy(tmp_path, stable_count):
    host = controller(tmp_path)
    for number in range(1, stable_count + 1):
        if number > 1:
            host.registry.releases[reference(number)] = release(122 + number, reference(number - 1))
        completed = host.deploy(reference(number))
        host.runtime.previous_stable = completed.stable
    host.registry.releases[reference(3)] = release(125, reference(stable_count))
    original = host.control.smoke

    def crash(runtime):
        raise SimulatedCrash()

    host.control.smoke = crash
    with pytest.raises(SimulatedCrash):
        host.deploy(reference(3))
    host.control.smoke = original
    return host, completed


def reload_controller(host):
    host.control = RolloutController(
        host.store,
        host.registry,
        host.runtime,
        "b" * 40,
        "c" * 64,
        smoke=host.control.smoke,
        readiness=host.control.readiness,
    )


@pytest.mark.parametrize("stable_count", [1, 2])
def test_failed_rollback_preflight_retries_the_interrupted_deploys_stable_target(
    tmp_path, stable_count
):
    host, before = interrupted_deploy(tmp_path, stable_count)
    host.registry.missing = True
    failed = host.rollback()
    assert failed.operation is not None and failed.operation.phase == "failed"
    assert failed.operation.target == before.stable
    assert failed.operation.requires_recovery
    assert failed.stable == before.stable

    reload_controller(host)
    refused = host.deploy(reference(3), resume=True)
    assert refused.refusal is not None and refused.refusal.reason == "interrupted-operation"
    host.registry.missing = False
    recovered = host.rollback()
    assert recovered.operation is not None and recovered.operation.phase == "rolled_back"
    assert recovered.operation.target == before.stable
    assert recovered.stable == before.stable
    assert not recovered.operation.recovery_pending


@pytest.mark.parametrize("stable_count", [1, 2])
def test_failed_resume_preflight_cannot_erase_the_interrupted_deploy_restrictions(
    tmp_path, stable_count
):
    host, before = interrupted_deploy(tmp_path, stable_count)
    host.registry.missing = True
    failed = host.deploy(reference(3), resume=True)
    assert failed.operation is not None and failed.operation.phase == "failed"
    assert failed.operation.requires_recovery

    reload_controller(host)
    for target, resume in ((reference(4), True), (reference(3), False)):
        refused = host.deploy(target, resume=resume)
        assert refused.refusal is not None and refused.refusal.reason == "interrupted-operation"
        assert refused.stable == before.stable
    host.registry.missing = False
    completed = host.deploy(reference(3), resume=True)
    assert completed.operation is not None and completed.operation.phase == "succeeded"
    assert completed.stable is not None and completed.stable.reference == reference(3)
    assert completed.previous_stable == before.stable
    assert not completed.operation.recovery_pending
