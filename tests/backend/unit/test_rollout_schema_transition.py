"""The soft-delete revision can advance and retreat from a real predecessor contract."""

from contextlib import nullcontext

import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.release_manifest import (
    ACCOUNT_LIFECYCLE_TRANSITION,
    SCHEMA,
    canonical,
    configuration,
    previous_configuration,
)
from tools.deploy.rollout_controller import ConfigurationTransition
from tools.deploy.rollout_schema import EXPECTED, SchemaSnapshot
from tools.deploy.rollout_state import RolloutError

OLD_REVISION = "20261019_1200_proposal_replay"


def historical_host(tmp_path, monkeypatch):
    host = controller(tmp_path)
    state = host.deploy(reference())
    previous = previous_configuration(
        configuration("local", False), transition=ACCOUNT_LIFECYCLE_TRANSITION
    )
    assert previous is not None and state.operation is not None
    old = release(125, deployed_configuration=previous, schema_revision=OLD_REVISION)
    old_ref = reference(3)
    host.registry.releases[old_ref] = old
    old_selection = host.store.remember(old_ref, old)
    data = state.model_dump(mode="json")
    data.update(stable=old_selection.model_dump(mode="json"), configuration_sha256="d" * 64)
    data["operation"]["target"] = data["stable"]
    data["operation"]["verification"]["schema_revision"] = OLD_REVISION
    host.store.path.write_bytes(canonical(data))
    host.control.transition = ConfigurationTransition("d" * 64, previous)
    host.registry.releases[reference(2)] = release(124, old_ref)
    old_tables = dict(
        EXPECTED,
        users=[
            c
            for c in EXPECTED["users"]
            if c
            not in {
                "deleted_at",
                "is_active_before_delete",
            }
        ],
    )
    old_schema = SchemaSnapshot(
        tables=old_tables,
        revisions=[OLD_REVISION],
        unsupported_objects=0,
        nontransactional_tables=0,
    )
    latest = host.runtime.snapshot
    host.runtime.snapshot = old_schema
    host.runtime.previous_stable = old_selection
    host.runtime.calls.clear()

    def migrate():
        assert host.runtime.snapshot == old_schema
        host.runtime.calls.append("upgrade")
        host.runtime.snapshot = latest

    def downgrade():
        assert host.runtime.snapshot == latest
        assert host.runtime.manifest == host.registry.releases[reference(2)]
        host.runtime.calls.append("downgrade")
        host.runtime.snapshot = old_schema

    monkeypatch.setattr(host.runtime, "migrate", migrate)
    monkeypatch.setattr(host.runtime, "downgrade_soft_delete", downgrade, raising=False)
    monkeypatch.setattr(host.runtime, "require_reversible_soft_delete", lambda: None, raising=False)
    monkeypatch.setattr(host.runtime, "predecessor_policy_guard", nullcontext)
    return host, old_selection


def test_existing_host_reaches_migration_and_records_new_schema(tmp_path, monkeypatch):
    host, old = historical_host(tmp_path, monkeypatch)
    before = host.store.load()
    assert before is not None and before.operation is not None
    assert before.operation.verification is not None
    assert before.operation.verification.schema_revision == OLD_REVISION

    after = host.deploy(reference(2))
    assert after.operation is not None and after.operation.phase == "succeeded"
    assert after.operation.verification is not None
    assert after.operation.verification.schema_revision == SCHEMA
    assert after.previous_stable == old
    assert host.runtime.calls.count("upgrade") == 1


def test_readiness_failure_rolls_back_to_actual_predecessor(tmp_path, monkeypatch):
    host, old = historical_host(tmp_path, monkeypatch)

    def readiness(runtime):
        if runtime.manifest == host.registry.releases[reference(2)]:
            raise RolloutError("readiness-failed")

    host.control.readiness = readiness
    result = host.deploy(reference(2))
    assert result.operation is not None and result.operation.phase == "rolled_back"
    assert result.operation.trigger_reason == "readiness-failed"
    assert result.operation.verification is not None
    assert result.operation.verification.schema_revision == OLD_REVISION
    assert result.stable == old
    assert host.runtime.snapshot.revisions == [OLD_REVISION]
    assert host.runtime.calls.count("downgrade") == 1


def test_outstanding_soft_delete_refuses_downgrade_without_changing_schema(tmp_path, monkeypatch):
    host, old = historical_host(tmp_path, monkeypatch)
    deployed = host.deploy(reference(2))
    assert deployed.operation is not None and deployed.operation.phase == "succeeded"

    def refuse():
        raise RolloutError("rollback-incompatible")

    monkeypatch.setattr(host.runtime, "require_reversible_soft_delete", refuse)
    result = host.rollback()
    assert result.refusal is not None and result.refusal.reason == "rollback-incompatible"
    assert result.operation == deployed.operation
    assert result.stable == deployed.stable and result.previous_stable == old
    assert host.runtime.snapshot.revisions == [SCHEMA]
    assert "downgrade" not in host.runtime.calls

    # A refused rollback must not block a healthy host from deploying forward.
    host.registry.releases[reference(4)] = release(126, reference(2))
    host.runtime.previous_stable = deployed.stable
    monkeypatch.setattr(host.runtime, "migrate", lambda: None)
    next_release = host.deploy(reference(4))
    assert next_release.operation is not None and next_release.operation.phase == "succeeded"


def test_resume_rolls_back_when_grants_fail_after_a_committed_migration(tmp_path, monkeypatch):
    host, old = historical_host(tmp_path, monkeypatch)
    migrate = host.runtime.migrate

    def interrupt_after_migration():
        migrate()
        raise KeyboardInterrupt()

    monkeypatch.setattr(host.runtime, "migrate", interrupt_after_migration)
    with pytest.raises(KeyboardInterrupt):
        host.deploy(reference(2))
    assert host.runtime.snapshot.revisions == [SCHEMA]

    def refuse_grants():
        raise RolloutError("migration-failed")

    monkeypatch.setattr(host.runtime, "migrate", lambda: None)
    monkeypatch.setattr(host.runtime, "apply_grants", refuse_grants)
    result = host.deploy(reference(2), resume=True)
    assert result.operation is not None and result.operation.phase == "rolled_back"
    assert result.operation.trigger_reason == "migration-failed"
    assert result.stable == old and host.runtime.snapshot.revisions == [OLD_REVISION]
