import copy

import pytest
from pydantic import ValidationError
from tests.backend.unit.test_release_manifest import manifest_data
from tools.deploy.release_manifest import SCHEMA, Manifest, ReleaseError, canonical, parse
from tools.deploy.rollout_state import (
    SMOKE_CHECKS,
    Event,
    HostState,
    Operation,
    RolloutError,
    Verification,
    selection,
)
from tools.deploy.rollout_store import StateStore


def selected():
    manifest = parse(canonical(manifest_data()), Manifest)
    return selection("ghcr.io/example/releases@sha256:" + "f" * 64, manifest)


def operation(
    phases=("preflight", "migration", "rollout", "readiness", "smoke", "succeeded"),
    *,
    target=None,
    schema_revision=SCHEMA,
):
    return Operation(
        operation_id="a" * 32,
        command="deploy",
        orchestrator_sha="b" * 40,
        target=target or selected(),
        verification=(
            Verification(
                schema_revision=schema_revision,
                image_identity_verified=True,
                database_volume_preserved=True,
                worker_progress_verified=True,
                smoke_checks=sorted(SMOKE_CHECKS),
            )
            if phases[-1] == "succeeded"
            else None
        ),
        events=[
            Event(phase=p, at=f"2026-11-05T00:00:{i:02d}Z", elapsed_seconds=float(i))
            for i, p in enumerate(phases)
        ],
    )


def test_atomic_promotion_requires_verified_terminal_selection():
    op = operation()
    state = HostState(
        project="prl-demo", configuration_sha256="c" * 64, stable=op.target, operation=op
    )
    assert parse(canonical(state), HostState) == state
    with pytest.raises(ValidationError):
        HostState(project="prl-demo", configuration_sha256="c" * 64, operation=op)


def test_state_store_reads_a_successful_predecessor_proof(tmp_path):
    data = manifest_data()
    data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    manifest = parse(canonical(data), Manifest)
    store = StateStore(tmp_path / "state")
    chosen = store.remember(
        "ghcr.io/example/releases@sha256:" + "e" * 64,
        manifest,
    )
    predecessor = operation(
        target=chosen,
        schema_revision="20261019_1200_proposal_replay",
    )
    state = HostState(
        project="prl-demo",
        configuration_sha256="c" * 64,
        stable=predecessor.target,
        operation=predecessor,
    )
    store.save(state)
    assert store.load() == state


def test_state_store_rejects_a_proof_pin_that_disagrees_with_its_release(tmp_path):
    manifest = parse(canonical(manifest_data()), Manifest)
    store = StateStore(tmp_path / "state")
    chosen = store.remember(
        "ghcr.io/example/releases@sha256:" + "e" * 64,
        manifest,
    )
    operation_with_wrong_pin = operation(
        target=chosen,
        schema_revision="20261019_1200_proposal_replay",
    )
    state = HostState(
        project="prl-demo",
        configuration_sha256="c" * 64,
        stable=chosen,
        operation=operation_with_wrong_pin,
    )
    store.save(state)
    with pytest.raises(RolloutError, match="invalid-state"):
        store.load()


@pytest.mark.parametrize(
    "phases",
    [
        ("preflight", "succeeded"),
        ("preflight", "rollout"),
        ("preflight", "migration", "preflight"),
        ("rollback_preflight",),
    ],
)
def test_partial_or_reordered_operations_cannot_claim_success(phases):
    with pytest.raises(ValidationError):
        operation(phases)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(format_version=True),
        lambda d: d.update(password="secret"),
        lambda d: d["operation"]["events"][1].update(elapsed_seconds=-1),
        lambda d: d["operation"]["events"][1].update(at="2026-11-05T00:00:01+07:00"),
        lambda d: d["operation"]["events"][1].update(reason="raw server exception"),
        lambda d: d["stable"]["images"].update(backend="example.invalid/image:latest"),
    ],
)
def test_journal_rejects_malformed_secrets_and_mutable_identity(change):
    op = operation()
    data = HostState(
        project="prl-demo", configuration_sha256="c" * 64, stable=op.target, operation=op
    ).model_dump()
    change(data)
    with pytest.raises(ReleaseError):
        parse(canonical(data), HostState)


def test_selections_pin_payload_and_all_shared_backend_consumers():
    first = selected()
    data = copy.deepcopy(manifest_data())
    data["created_at"] = "2026-11-05T00:00:00Z"
    second = selection(first.reference, parse(canonical(data), Manifest))
    assert first.source_sha == second.source_sha
    assert first.payload_sha256 != second.payload_sha256
    assert set(first.images) == {"backend", "frontend"}


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(verification=None),
        lambda d: d["verification"].update(smoke_checks=["login"]),
        lambda d: d["verification"].update(worker_progress_verified=False),
        lambda d: d["verification"].update(database_volume_preserved=False),
    ],
)
def test_terminal_phase_cannot_replace_missing_service_verification(change):
    data = operation().model_dump()
    change(data)
    with pytest.raises(ReleaseError):
        parse(canonical(data), Operation)
