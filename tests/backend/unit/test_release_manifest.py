"""Release parsing/provenance invariants; no registry or Docker required."""

import copy
import hashlib
import json
import subprocess
import sys

import pytest
from tools.deploy.release import render
from tools.deploy.release_manifest import (
    ADMIN_POLICY_TRANSITION,
    CHECKS,
    ROOT,
    SCHEMA,
    Manifest,
    ReleaseError,
    Spec,
    canonical,
    configuration,
    parse,
    previous_configuration,
    proof,
    read,
    require_compatible,
    require_configuration,
    vendor_inventory,
    write,
)


def spec_data():
    return {
        "format": "prl-candidate",
        "format_version": 1,
        "release_id": "release-123-1",
        "created_at": "2026-11-03T00:00:00Z",
        "provenance": {
            "repository": "4PMs-3Workers/Production-Readiness-Lab",
            "source_sha": "a" * 40,
            "pr_number": 99,
            "pr_head_sha": "b" * 40,
            "pr_merge_sha": "a" * 40,
            "run_id": 123,
            "run_attempt": 1,
            "workflow_ref": (
                "4PMs-3Workers/Production-Readiness-Lab/"
                ".github/workflows/release.yml@refs/heads/develop"
            ),
        },
        "images": {
            name: {
                "reference": f"ghcr.io/example/prl/{name}@sha256:" + character * 64,
                "image_id": "sha256:" + character * 64,
                "archive_sha256": character * 64,
                "platform": "linux/amd64",
            }
            for name, character in (("backend", "1"), ("frontend", "2"))
        },
        "services": dict(
            dict.fromkeys(["api", "worker", "schema", "migrate", "seed", "backup"], "backend"),
            frontend="frontend",
        ),
        "vendor_images": vendor_inventory(False),
        "accepted_schema_revisions": [SCHEMA],
        "configuration": configuration("local", False).model_dump(),
        "previous_stable": None,
        "bootstrap": True,
    }


def manifest_data():
    data = spec_data()
    spec = parse(canonical(data), Spec)
    data.update(
        format="prl-release",
        candidate_proof=proof(spec, "candidate").model_dump(),
        published_proof=proof(spec, "published").model_dump(),
        source_checks=sorted(CHECKS),
        candidate_artifact_id=456,
    )
    return data


def test_roundtrip_atomic_write_and_separate_pr_head(tmp_path):
    manifest = parse(canonical(manifest_data()), Manifest)
    path = tmp_path / "release.json"
    write(path, manifest)
    assert read(path, Manifest) == manifest
    assert list(tmp_path.iterdir()) == [path]
    assert manifest.provenance.pr_head_sha != manifest.provenance.source_sha
    require_configuration(manifest.configuration)


def test_reviewed_predecessor_schema_requires_rollback_opt_in():
    data = spec_data()
    data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    predecessor = parse(canonical(data), Spec)
    with pytest.raises(ReleaseError, match="incompatible-schema"):
        require_compatible(predecessor)
    require_compatible(predecessor, allow_previous=True)


def test_reviewed_predecessor_requires_opt_in_and_exact_configuration(tmp_path):
    current = configuration("local", False)
    previous = previous_configuration(current)
    assert previous is not None
    previous_hashes = {item.path: item.sha256 for item in previous.files}
    assert previous_hashes["Dockerfile.backend"] == (
        "b4845317377f7bb32900542e20c5a147852cf8704f32cae404eb4d8582a38d3c"
    )
    assert previous_hashes["Dockerfile.frontend"] == (
        "b0569fa1f5faf1732cb14c7eb91aa231372c0922cd4f11a3173825d72fb8599a"
    )
    assert previous_hashes["tools/deploy/compose.local.yaml"] == (
        "5766741d8e44c72fc5487cd689ca658a6780e9f8cf804481df4f3a9a89bbb758"
    )
    assert previous_hashes["tools/deploy/nginx/nginx.conf"] == (
        "dd8331ac797a1f5bfa5c05eb3a33af815fd188346058860d9e9e27e631343597"
    )
    assert previous_hashes["tools/deploy/nginx/routes.conf"] == (
        "ebfdcf0a4efcf8f13b8a9423e69ed251ea70a5ebc8193eefcf4d477b4ed5fc22"
    )
    assert previous_hashes["tools/deploy/compose.yaml"] == (
        "1f9c939fb1496429e6870000e1e2c4ccb2a5773a402984d70b1bfd27ca15833e"
    )
    assert previous_hashes["tools/deploy/mysql/grants.sql"] == (
        "8a7277032db2552dc45e9031dd0947d1b0aa363b0d1ebd445366225af3af038a"
    )
    assert previous_hashes["tools/infra/images.lock.json"] == (
        "63e615673101a54601bba4c5a40fc4b6b00f55b0624d51356a31639214b5c76f"
    )
    https_previous = previous_configuration(configuration("https", False))
    assert https_previous is not None
    https_hashes = {item.path: item.sha256 for item in https_previous.files}
    assert "tools/deploy/compose.local.yaml" not in https_hashes
    assert https_hashes["tools/deploy/nginx/nginx.conf"] == (
        "dd8331ac797a1f5bfa5c05eb3a33af815fd188346058860d9e9e27e631343597"
    )
    assert https_hashes["tools/deploy/nginx/routes.conf"] == (
        "ebfdcf0a4efcf8f13b8a9423e69ed251ea70a5ebc8193eefcf4d477b4ed5fc22"
    )
    assert https_hashes["tools/deploy/compose.https.yaml"] == (
        "0811f0b010706d59351e7f3a0c4951b9f788731913877c1d88a805be1c0ad305"
    )
    with pytest.raises(ReleaseError, match="incompatible-configuration"):
        require_configuration(previous)
    require_configuration(previous, allow_previous=True)

    for item in current.files:
        path = tmp_path / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / item.path).read_bytes())
    # Changes outside the explicitly reviewed files cannot use the transition.
    with (tmp_path / "tools/deploy/nginx/error-input.conf").open("ab") as output:
        output.write(b"\n# unreviewed\n")
    with pytest.raises(ReleaseError, match="incompatible-configuration"):
        require_configuration(previous, tmp_path, allow_previous=True)
    # A third version of either reviewed file cannot use this transition either.
    with (tmp_path / "tools/deploy/mysql/grants.sql").open("ab") as output:
        output.write(b"\n-- unreviewed\n")
    assert previous_configuration(configuration("local", False, tmp_path)) is None


def test_reviewed_transition_is_the_exact_pr_base_configuration():
    current = configuration("local", False)
    previous = previous_configuration(current)
    assert previous is not None
    expected_base_hashes = {
        path: before for path, (before, _after) in ADMIN_POLICY_TRANSITION.items()
    }
    actual_base_hashes = {item.path: item.sha256 for item in previous.files}
    assert all(actual_base_hashes[path] == digest for path, digest in expected_base_hashes.items())
    for path, digest in expected_base_hashes.items():
        fixture = ROOT / "tests/backend/unit/fixtures/pr66-base" / path.rsplit("/", 1)[-1]
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == digest
        assert (
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            == ADMIN_POLICY_TRANSITION[path][1]
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(format_version=2),
        lambda d: d.pop("format_version"),
        lambda d: d.pop("format"),
        lambda d: d.update(format_version=True),
        lambda d: d.update(password="synthetic-secret"),
        lambda d: d.pop("images"),
        lambda d: d.update(created_at="tomorrow"),
        lambda d: d.update(created_at="2026-11-03T00:00:00+07:00"),
        lambda d: d["provenance"].update(source_sha="abc"),
        lambda d: d["provenance"].update(run_id=True),
        lambda d: d["images"]["frontend"].update(reference="ghcr.io/example/prl:latest"),
        lambda d: d["images"]["backend"].update(image_id="sha256:bad"),
        lambda d: d["images"]["backend"].update(platform="linux/arm64"),
        lambda d: d["services"].update(worker="frontend"),
        lambda d: d["services"].update(api="frontend"),
        lambda d: d["services"].update(unknown="backend"),
        lambda d: d.update(accepted_schema_revisions=[]),
        lambda d: d.update(bootstrap=False),
        lambda d: d.update(previous_stable="ghcr.io/example/releases:stable"),
        lambda d: d["configuration"].update(sha256="0" * 64),
        lambda d: d["configuration"].update(observability=True),
        lambda d: d.update(source_checks=["backend"]),
        lambda d: d["candidate_proof"]["provenance"].update(run_attempt=2),
        lambda d: d["published_proof"].update(stage="candidate"),
        lambda d: d["candidate_proof"]["image_digests"].update(frontend="sha256:" + "f" * 64),
    ],
)
def test_invalid_contracts_fail_closed(change):
    data = manifest_data()
    change(data)
    with pytest.raises(ReleaseError):
        parse(canonical(data), Manifest)


@pytest.mark.parametrize(
    "raw",
    [b"{}", b"[]", b'{"x":1,"x":2}', b"{", b'{"x":NaN}', b"x" * 1048577],
    ids=["empty", "array", "duplicate", "truncated", "nonfinite", "oversized"],
)
def test_malformed_json(raw):
    with pytest.raises(ReleaseError):
        parse(raw, Manifest)


def test_same_source_rebuild_cannot_reuse_candidate_proof():
    data = manifest_data()
    original = data["provenance"]["source_sha"]
    data["images"]["frontend"]["reference"] = "ghcr.io/example/prl/frontend@sha256:" + "f" * 64
    assert data["provenance"]["source_sha"] == original
    with pytest.raises(ReleaseError, match="candidate-mismatch"):
        parse(canonical(data), Manifest)


def test_historical_schema_is_readable_but_cannot_be_consumed():
    data = manifest_data()
    data["accepted_schema_revisions"] = ["historical_revision"]
    historical = parse(canonical(data), Manifest)
    with pytest.raises(ReleaseError, match="incompatible-schema"):
        require_compatible(historical)


def test_config_drift_and_secret_files_are_not_read(tmp_path):
    config = configuration("local", False)
    for file in config.files:
        target = tmp_path / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / file.path).read_bytes())
    (tmp_path / ".env").write_text("password=synthetic-secret")
    assert configuration("local", False, tmp_path) == config
    target = tmp_path / config.files[0].path
    target.write_bytes(target.read_bytes() + b"\n# drift")
    with pytest.raises(ReleaseError, match="incompatible-configuration"):
        require_configuration(config, tmp_path)
    assert b"synthetic-secret" not in canonical(config)


@pytest.mark.parametrize("overlay", ["local", "https"])
@pytest.mark.parametrize(
    "filename",
    [
        "nginx.conf",
        "gateway-entrypoint.sh",
        "routes.conf",
        "proxy.conf",
        "route-map.conf",
        "file-sink.conf",
        "error-input.conf",
        "server-profile",
    ],
)
def test_gateway_runtime_file_drift_invalidates_configuration(tmp_path, overlay, filename):
    filename = (
        ("local.conf" if overlay == "local" else "https.conf")
        if filename == "server-profile"
        else filename
    )
    config = configuration(overlay, False)
    relative = "tools/deploy/nginx/" + filename
    assert relative in {file.path for file in config.files}
    for file in config.files:
        target = tmp_path / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / file.path).read_bytes())
    require_configuration(config, tmp_path)
    target = tmp_path / relative
    target.write_bytes(target.read_bytes() + b"\n# gateway behavior changed\n")
    with pytest.raises(ReleaseError, match="incompatible-configuration"):
        require_configuration(config, tmp_path)


def test_render_covers_operational_backend_without_build_fallback():
    spec = parse(canonical(spec_data()), Spec)
    text = render(spec, backup=True).decode()
    assert text.count("build: !reset null") == len(spec.services)
    assert text.count(spec.images["backend"].reference) == 6
    assert "latest" not in text
    assert "ACCEPTED_SCHEMA_REVISIONS: " + SCHEMA in text


def test_rendered_predecessor_keeps_its_schema_pin():
    data = spec_data()
    data["accepted_schema_revisions"] = ["20261019_1200_proposal_replay"]
    spec = parse(canonical(data), Spec)
    text = render(spec, allow_previous=True).decode()
    assert "ACCEPTED_SCHEMA_REVISIONS: 20261019_1200_proposal_replay" in text
    assert "ACCEPTED_SCHEMA_REVISIONS: " + SCHEMA not in text


def test_unknown_versions_do_not_reinterpret_deployment_evidence():
    from tools.deploy import evidence

    v1 = evidence.create("a" * 40, "local", "config", "passed", {})
    assert evidence.validate(copy.deepcopy(v1)) == v1
    v2 = evidence.create_v2("a" * 40, "local", False, "0" * 64, "config", "passed", {})
    assert evidence.validate(copy.deepcopy(v2)) == v2
    for historical in (v1, v2):
        historical["schema_revisions"] = ["20261019_1200_proposal_replay"]
        assert evidence.validate(copy.deepcopy(historical)) == historical
    for old in (v1, v2):
        with pytest.raises(ReleaseError):
            parse(canonical(old), Manifest)


def test_observability_derived_exporter_uses_the_selected_backend_digest():
    data = spec_data()
    data["configuration"] = configuration("https", True).model_dump()
    data["vendor_images"] = vendor_inventory(True)
    data["images"]["collector"] = dict(data["images"]["backend"])
    data["services"].update(collector="collector", local_alert_receiver="backend")
    spec = parse(canonical(data), Spec)
    text = render(spec).decode()
    assert "  backup_metrics_exporter:\n    image: " + spec.images["backend"].reference in text
    assert "prl-backend:local" not in text
    assert "SERVICE_RELEASE: " + spec.provenance.source_sha in text


def test_json_schema_is_current():
    expected = json.loads((ROOT / "tools/deploy/release-manifest.schema.json").read_bytes())
    assert Manifest.model_json_schema() == expected


def test_cli_sanitizes_errors_and_missing_artifacts(tmp_path):
    path = tmp_path / "malformed.json"
    path.write_text('{"password":"synthetic-secret"}')
    for argument in (str(path), str(tmp_path / "absent.json"), "synthetic-secret"):
        result = subprocess.run(
            [sys.executable, "-m", "tools.deploy.release", "validate", argument],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=15,
        )
        assert result.returncode == 1
        assert "synthetic-secret" not in result.stdout + result.stderr
        assert "Traceback" not in result.stdout + result.stderr
