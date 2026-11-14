import pytest
import tools.deploy.rollout_controller as rollout_controller
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.release_manifest import Manifest, canonical, parse
from tools.deploy.rollout_preflight import Prepared
from tools.deploy.rollout_schema import EXPECTED, PREDECESSOR_EXPECTED, SchemaSnapshot
from tools.deploy.rollout_state import HostState, selection


def test_upgrade_preflight_uses_the_previous_stable_schema_before_migration(tmp_path):
    host = controller(tmp_path)
    previous_data = release().model_dump(mode="json")
    previous_data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    previous = parse(canonical(previous_data), Manifest)
    previous_ref = reference()
    host.registry.releases[previous_ref] = previous
    stable = host.store.remember(previous_ref, previous)
    state = HostState(
        project=host.runtime.project,
        configuration_sha256="c" * 64,
        stable=stable,
    )

    candidate = release(124, previous_ref)
    prepared = Prepared(selection(reference(2), candidate), candidate)

    assert host.control._migration_input_schema_revisions(state, prepared) == [
        "20261019_1200_proposal_replay"
    ]


def test_upgrade_checks_predecessor_schema_until_migration_completes(tmp_path, monkeypatch):
    host = controller(tmp_path)
    previous_data = release().model_dump(mode="json")
    previous_data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    previous = parse(canonical(previous_data), Manifest)
    previous_ref = reference()
    host.registry.releases[previous_ref] = previous
    stable = host.store.remember(previous_ref, previous)
    host.runtime.previous_stable = stable
    host.runtime.snapshot = SchemaSnapshot(
        tables=PREDECESSOR_EXPECTED,
        revisions=["20261019_1200_proposal_replay"],
        unsupported_objects=0,
        nontransactional_tables=0,
    )
    host.store.save(
        HostState(
            project=host.runtime.project,
            configuration_sha256=host.control.configuration_sha256,
            stable=stable,
        )
    )
    candidate_ref = reference(2)
    host.registry.releases[candidate_ref] = release(124, previous_ref)
    original_require_schema = rollout_controller.require_schema
    accepted_revisions = []

    def record_schema(snapshot, accepted, *, allow_empty=False):
        accepted_revisions.append(accepted)
        return original_require_schema(snapshot, accepted, allow_empty=allow_empty)

    def migrate():
        host.runtime.calls.append("migrate")
        host.runtime.snapshot = SchemaSnapshot(
            tables=EXPECTED,
            revisions=["20261113_1200_booking_quota"],
            unsupported_objects=0,
            nontransactional_tables=0,
        )

    monkeypatch.setattr(rollout_controller, "require_schema", record_schema)
    monkeypatch.setattr(host.runtime, "migrate", migrate)

    result = host.deploy(candidate_ref)

    assert result.operation is not None and result.operation.phase == "succeeded"
    assert accepted_revisions[:2] == [
        ["20261019_1200_proposal_replay"],
        ["20261019_1200_proposal_replay"],
    ]


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
