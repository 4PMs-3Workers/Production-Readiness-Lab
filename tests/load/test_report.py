import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from tools.deploy.evidence import create_target_attestation
from tools.load.metrics import summarize_gateway_events
from tools.load.report import (
    MAX_REPORT_BYTES,
    _deployed_artifact_identity,
    build_report,
    write_report,
)
from tools.load.runner import WorkloadResult


def report():
    now = datetime(2026, 11, 8, tzinfo=UTC)
    workload = WorkloadResult(20, 20, 600, now, now + timedelta(seconds=600))
    sli = summarize_gateway_events(
        [],
        duration_seconds=600,
        minimum_samples=1,
    )
    return build_report(
        target_url="https://user:password@candidate.invalid/?token=private",
        environment="demo",
        candidate_release_sha="a" * 40,
        provenance={"checkout_source_sha": "b" * 40, "configuration_sha256": {}},
        dataset={"staff": 10, "appointments": 1_000},
        workload=workload,
        sli=sli,
        runtime_signals={"signals": {}},
        host_start={"cpu_count": 8},
        host_end={"cpu_count": 8},
        invariant_results={"passed": True},
    )


def test_report_is_sanitized_and_does_not_claim_a_seven_day_slo():
    payload = report()
    encoded = json.dumps(payload)
    target = cast(dict[str, object], payload["target"])
    interpretation = cast(dict[str, object], payload["interpretation"])
    gateway_sli = cast(dict[str, object], payload["gateway_sli"])
    availability = cast(dict[str, object], gateway_sli["availability"])

    assert "password" not in encoded
    assert "private" not in encoded
    assert "user@" not in encoded
    assert target["origin"] == "https://candidate.invalid"
    assert interpretation["seven_day_slo_claimed"] is False
    assert availability["status"] == "insufficient_evidence"


def test_report_writer_is_bounded_and_writes_valid_json(tmp_path):
    target = tmp_path / "reports" / "run.json"
    write_report(target, report())
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1

    with pytest.raises(ValueError, match="256 KiB"):
        write_report(target, {"oversized": "x" * (MAX_REPORT_BYTES + 1)})


def _write_deployment_manifest(tmp_path, *, missing_image_id=False):
    source_sha = "a" * 40
    manifest = tmp_path / "build" / "deploy" / source_sha / "manifest.json"
    manifest.parent.mkdir(parents=True)
    images = {
        name: {
            "id": "sha256:" + index * 64,
            "digests": [f"registry.invalid/{name}@sha256:{index * 64}"],
        }
        for name, index in (("api", "a"), ("frontend", "b"), ("worker", "c"), ("gateway", "d"))
    }
    if missing_image_id:
        images["api"]["id"] = ""
    value = {
        "manifest_version": 2,
        "source_sha": source_sha,
        "overlay": "local",
        "schema_revisions": ["20261019_1200_proposal_replay"],
        "verification": {"command": "evidence", "status": "passed"},
        "created_at": "2026-11-08T00:00:00Z",
        "updated_at": "2026-11-08T00:00:00Z",
        "observability": False,
        "configuration_sha256": "e" * 64,
        "observability_verification": None,
        "deployment_state": "active",
        "target_origin": "https://candidate.invalid",
        "images": images,
    }
    manifest.write_text(json.dumps(value), encoding="utf-8")
    if not missing_image_id:
        (manifest.parent / "attestation.json").write_text(
            json.dumps(create_target_attestation(value)), encoding="utf-8"
        )
    return source_sha


def test_deployed_artifact_identity_requires_matching_passing_manifest(tmp_path):
    source_sha = _write_deployment_manifest(tmp_path)

    result = _deployed_artifact_identity(
        tmp_path, source_sha, target_url="https://candidate.invalid/"
    )

    assert result["manifest_complete"] is True
    assert result["target_artifact_identity_complete"] is False
    assert result["configured_target_origin_matches"] is True
    assert result["target_association"] == "origin_matches_but_runtime_unverified"
    assert result["configuration_sha256"] == "e" * 64
    image_digests = cast(dict[str, list[str]], result["image_digests"])
    assert set(image_digests) == {"api", "frontend", "worker", "gateway"}
    attestation = json.loads(
        (tmp_path / "build" / "deploy" / source_sha / "attestation.json").read_text()
    )
    runtime_result = _deployed_artifact_identity(
        tmp_path,
        source_sha,
        target_url="https://candidate.invalid/",
        target_attestation_sha256=attestation["attestation_sha256"],
    )
    assert runtime_result["target_artifact_identity_complete"] is True
    assert runtime_result["target_association"] == "runtime_attestation_matches_local_manifest"
    assert runtime_result["runtime_attestation_sha256"] == attestation["attestation_sha256"]
    assert _deployed_artifact_identity(tmp_path, "f" * 40)["status"] == "unavailable"
    assert (
        _deployed_artifact_identity(tmp_path, source_sha, target_url="https://different.invalid")[
            "target_artifact_identity_complete"
        ]
        is False
    )


def test_deployed_artifact_identity_rejects_incomplete_image_identity(tmp_path):
    source_sha = _write_deployment_manifest(tmp_path, missing_image_id=True)
    result = _deployed_artifact_identity(
        tmp_path, source_sha, target_url="https://candidate.invalid"
    )

    assert result["manifest_complete"] is False
    assert result["target_artifact_identity_complete"] is False
