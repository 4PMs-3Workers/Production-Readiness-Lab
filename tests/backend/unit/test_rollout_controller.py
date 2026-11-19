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


def test_resume_after_migration_uses_and_validates_the_candidate_catalog(tmp_path, monkeypatch):
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

    def migrate_to_candidate():
        host.runtime.calls.append("migrate")
        host.runtime.snapshot = SchemaSnapshot(
            tables=EXPECTED,
            revisions=["20261113_1200_booking_quota"],
            unsupported_objects=0,
            nontransactional_tables=0,
        )

    monkeypatch.setattr(host.runtime, "migrate", migrate_to_candidate)
    fail_once = True
    original_grants = host.runtime.apply_grants

    def fail_after_migration():
        nonlocal fail_once
        host.runtime.calls.append("grants")
        if fail_once:
            fail_once = False
            from tools.deploy.rollout_state import RolloutError

            raise RolloutError("migration-failed")

    monkeypatch.setattr(host.runtime, "apply_grants", fail_after_migration)
    interrupted = host.deploy(candidate_ref)
    assert interrupted.operation is not None and interrupted.operation.phase == "failed"
    assert interrupted.operation.requires_recovery
    assert host.runtime.snapshot.revisions == ["20261113_1200_booking_quota"]

    monkeypatch.setattr(host.runtime, "apply_grants", original_grants)
    resumed = host.deploy(candidate_ref, resume=True)
    assert resumed.operation is not None and resumed.operation.phase == "succeeded"
    assert resumed.stable is not None and resumed.stable.reference == candidate_ref


def test_resume_is_allowed_when_automatic_fallback_was_refused_at_preflight(tmp_path, monkeypatch):
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

    def migrate_to_candidate():
        host.runtime.calls.append("migrate")
        host.runtime.snapshot = SchemaSnapshot(
            tables=EXPECTED,
            revisions=["20261113_1200_booking_quota"],
            unsupported_objects=0,
            nontransactional_tables=0,
        )

    host.runtime.migrate = migrate_to_candidate
    readiness_fails_once = True

    def readiness(_runtime):
        nonlocal readiness_fails_once
        host.runtime.calls.append("ready")
        if readiness_fails_once:
            readiness_fails_once = False
            from tools.deploy.rollout_state import RolloutError

            raise RolloutError("readiness-failed")

    host.control.readiness = readiness
    refused_fallback = host.deploy(candidate_ref)
    assert refused_fallback.operation is not None
    assert refused_fallback.operation.phase == "failed"
    assert refused_fallback.operation.events[-1].reason == "schema-incompatible"
    phases = [event.phase for event in refused_fallback.operation.events]
    assert "rollback_preflight" in phases
    assert "rollback_rollout" not in phases
    assert refused_fallback.operation.requires_recovery

    resumed = host.deploy(candidate_ref, resume=True)
    assert resumed.operation is not None and resumed.operation.phase == "succeeded"
    assert resumed.stable is not None and resumed.stable.reference == candidate_ref


def test_refused_rollback_does_not_block_a_later_compatible_deploy(tmp_path):
    host = controller(tmp_path)
    old_data = release().model_dump(mode="json")
    old_data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    old = parse(canonical(old_data), Manifest)
    old_ref = reference()
    host.registry.releases[old_ref] = old
    stable = host.store.remember(old_ref, old)
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

    def migrate_to_current():
        host.runtime.calls.append("migrate")
        host.runtime.snapshot = SchemaSnapshot(
            tables=EXPECTED,
            revisions=["20261113_1200_booking_quota"],
            unsupported_objects=0,
            nontransactional_tables=0,
        )

    host.runtime.migrate = migrate_to_current
    candidate_ref = reference(2)
    host.registry.releases[candidate_ref] = release(124, old_ref)
    current = host.deploy(candidate_ref)
    assert current.operation is not None and current.operation.phase == "succeeded"
    assert current.previous_stable == stable

    refused = host.rollback()
    assert refused.refusal is not None and refused.refusal.reason == "schema-incompatible"
    assert refused.operation is not None
    assert refused.operation == current.operation
    assert not refused.operation.requires_recovery

    next_ref = reference(3)
    host.registry.releases[next_ref] = release(125, candidate_ref)
    host.runtime.previous_stable = current.stable
    deployed = host.deploy(next_ref)
    assert deployed.operation is not None and deployed.operation.phase == "succeeded"
    assert deployed.stable is not None and deployed.stable.reference == next_ref


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
