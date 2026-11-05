import copy

import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.release_manifest import ReleaseError, canonical, parse
from tools.deploy.rollout_evidence import LabReport


def lab_data(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    host.runtime.previous_stable = before.stable
    image = (
        host.registry.releases[reference()]
        .images["backend"]
        .model_copy(
            update={
                "reference": "ghcr.io/example/backend@sha256:" + "3" * 64,
                "image_id": "sha256:" + "3" * 64,
            }
        )
    )
    host.registry.releases[reference(2)] = release(124, reference(), backend=image)
    host.runtime.fail, host.runtime.fail_release = "smoke", "release-124-1"
    state = host.deploy(reference(2))
    assert state.operation is not None and state.stable is not None
    op = state.operation
    decided = next(e for e in op.events if e.phase == "rollback_preflight")
    ready = next(e for e in op.events if e.phase == "rollback_smoke")
    return dict(
        format="prl-rollback-lab",
        format_version=1,
        proof_kind="automated-linux-compose-lab",
        fixture_review_metadata=True,
        source_sha=op.orchestrator_sha,
        pr_head_sha="c" * 40,
        stable_manifest=reference(),
        bad_manifest=reference(2),
        configuration_sha256=state.stable.configuration_sha256,
        rollback_decided_at=decided.at,
        readiness_completed_at=ready.at,
        smoke_completed_at=op.events[-1].at,
        rollback_seconds=op.events[-1].elapsed_seconds - decided.elapsed_seconds,
        restored_images=state.stable.images,
        restored_image_ids=state.stable.image_ids,
        bad_backend_image_id=image.image_id,
        committed_before=1,
        committed_during=1,
        committed_records_preserved=True,
        replay_and_effects_verified=True,
        concurrent_rollback_refused=True,
        database_volume_preserved=True,
        negative_preflight_cases=dict(
            missing_images="missing-images", incompatible_configuration="incompatible-configuration"
        ),
        result=state.model_dump(mode="json"),
        outcome="passed",
    )


def test_lab_evidence_keeps_actual_source_and_fixture_publication_scope_explicit(tmp_path):
    data = lab_data(tmp_path)
    report = parse(canonical(data), LabReport)
    assert report.source_sha != report.pr_head_sha
    assert report.fixture_review_metadata is True
    assert report.proof_kind == "automated-linux-compose-lab"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(password="private"),
        lambda d: d.update(fixture_review_metadata=False),
        lambda d: d.update(rollback_seconds=301.0),
        lambda d: d.update(committed_records_preserved=False),
        lambda d: d.update(rollback_decided_at="2020-01-01T00:00:00Z"),
        lambda d: d.update(restored_image_ids={"backend": "sha256:" + "f" * 64}),
        lambda d: d.update(negative_preflight_cases={}),
    ],
)
def test_partial_tampered_or_secret_bearing_proof_is_refused(tmp_path, change):
    data = copy.deepcopy(lab_data(tmp_path))
    change(data)
    with pytest.raises(ReleaseError):
        parse(canonical(data), LabReport)
