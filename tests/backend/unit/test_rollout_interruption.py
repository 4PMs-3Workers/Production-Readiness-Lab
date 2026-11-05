import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release


class SimulatedCrash(BaseException):
    pass


def test_crash_during_smoke_preserves_stable_and_requires_explicit_recovery(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    host.runtime.previous_stable = before.stable
    host.registry.releases[reference(2)] = release(124, reference())

    def crash(runtime):
        raise SimulatedCrash()

    original = host.control.smoke
    host.control.smoke = crash
    with pytest.raises(SimulatedCrash):
        host.deploy(reference(2))
    interrupted = host.store.load()
    assert interrupted is not None and interrupted.operation is not None
    assert interrupted.operation.phase == "smoke" and interrupted.stable == before.stable
    host.control.smoke = original
    refused = host.deploy(reference(2))
    assert refused.refusal is not None and refused.refusal.reason == "interrupted-operation"
    assert refused.stable == before.stable
    recovered = host.rollback()
    assert recovered.operation is not None and recovered.operation.phase == "rolled_back"
    assert recovered.stable == before.stable


def test_explicit_resume_repeats_verification_and_cannot_switch_target(tmp_path):
    host = controller(tmp_path)
    host.runtime.fail = "smoke"
    failed = host.deploy(reference())
    wrong = host.deploy(reference(2), resume=True)
    assert wrong.refusal is not None and wrong.refusal.reason == "interrupted-operation"
    assert wrong.stable == failed.stable is None
    host.runtime.fail = None
    result = host.deploy(reference(), resume=True)
    assert result.operation is not None and result.operation.phase == "succeeded"
    assert host.runtime.calls.count("smoke") == 2


def test_verified_success_retry_does_not_make_the_stable_pointer_its_own_predecessor(tmp_path):
    host = controller(tmp_path)
    first = host.deploy(reference())
    host.runtime.previous_stable = first.stable
    retried = host.deploy(reference(), resume=True)
    assert retried.stable == first.stable and retried.previous_stable is None
    assert retried.operation is not None and first.operation is not None
    assert retried.operation.operation_id != first.operation.operation_id


def test_post_replace_io_error_does_not_revert_a_verified_stable_pointer(tmp_path, monkeypatch):
    host = controller(tmp_path)
    save, injected = host.store.save, [False]

    def fail_after_commit(state):
        save(state)
        if state.operation is not None and state.operation.phase == "succeeded" and not injected[0]:
            injected[0] = True
            raise OSError("private filesystem error")

    monkeypatch.setattr(host.store, "save", fail_after_commit)
    result = host.deploy(reference())
    assert result.refusal is not None and result.refusal.reason == "invalid-state"
    assert result.stable is not None and result.stable.reference == reference()
    assert host.store.load() == result


def test_resume_cannot_restart_a_target_after_automatic_rollback_was_decided(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    host.runtime.previous_stable = before.stable
    host.registry.releases[reference(2)] = release(124, reference())
    host.runtime.fail = "smoke"
    failed = host.deploy(reference(2))
    assert failed.operation is not None and failed.operation.trigger_reason == "smoke-failed"
    refused = host.deploy(reference(2), resume=True)
    assert refused.refusal is not None and refused.refusal.reason == "interrupted-operation"
    assert refused.stable == before.stable
