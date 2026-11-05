import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release


def test_stable_promotion_occurs_only_after_all_live_verification(tmp_path):
    host = controller(tmp_path)
    result = host.deploy(reference())
    assert result.operation is not None and result.operation.phase == "succeeded"
    assert result.stable is not None and result.stable.reference == reference()
    assert host.runtime.calls == [
        "plan",
        "images",
        "migrate",
        "grants",
        "rollout",
        "ready",
        "verify-images",
        "smoke",
        "verify-images",
    ]
    assert host.store.load() == result


@pytest.mark.parametrize("failure", ["migration", "rollout", "smoke"])
def test_failure_without_stable_never_promotes_partial_release(tmp_path, failure):
    host = controller(tmp_path)
    host.runtime.fail = failure
    result = host.deploy(reference())
    assert result.operation is not None
    assert result.stable is None and result.operation.phase == "failed"
    assert result.operation.events[-1].reason == failure + "-failed"


def test_success_retains_previous_stable_and_rejects_wrong_release_chain(tmp_path):
    host = controller(tmp_path)
    old = host.deploy(reference()).stable
    host.runtime.previous_stable = old
    host.registry.releases[reference(2)] = release(124, reference())
    result = host.deploy(reference(2))
    assert result.stable is not None
    assert result.previous_stable == old and result.stable.reference == reference(2)
    host.registry.releases[reference(3)] = release(125, reference())
    failed = host.deploy(reference(3))
    assert failed.operation is not None
    assert failed.stable == result.stable and failed.operation.phase == "failed"
    assert failed.operation.events[-1].reason == "invalid-manifest"


def test_partial_schema_is_refused_before_migration_or_rollout(tmp_path):
    host = controller(tmp_path)
    data = host.runtime.snapshot.model_dump()
    data["tables"].pop("users")
    host.runtime.snapshot = type(host.runtime.snapshot)(**data)
    result = host.deploy(reference())
    assert result.operation is not None
    assert result.operation.phase == "failed"
    assert "migrate" not in host.runtime.calls and "rollout" not in host.runtime.calls
