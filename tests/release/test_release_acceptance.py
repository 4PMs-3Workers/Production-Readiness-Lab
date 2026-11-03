"""Required release checks fail for absent prerequisites rather than skip."""

import os
import shutil

import pytest
from tools.deploy.release_images import Registry, command
from tools.deploy.release_manifest import (
    Manifest,
    ReleaseError,
    Spec,
    canonical,
    parse,
    proof,
    read,
    write,
)


def test_exact_candidate_is_consumed_without_rebuilding(tmp_path):
    from pathlib import Path

    from tests.release.acceptance import consume

    for tool in ("docker", "skopeo", "oras"):
        assert shutil.which(tool), f"Required release prerequisite missing: {tool}"
    assert os.environ.get("RELEASE_SPEC"), "RELEASE_SPEC is required; acceptance cannot skip"
    assert os.environ.get("RELEASE_PROOF"), "RELEASE_PROOF is required"
    stage = os.environ.get("RELEASE_STAGE")
    assert stage in {"candidate", "published"}
    command(["docker", "info", "--format", "{{.ServerVersion}}"])
    spec = read(Path(os.environ["RELEASE_SPEC"]), Spec)
    target = os.environ.get("RELEASE_LAB_REPOSITORY")
    registry = Registry(plain_http=bool(target))
    consumed = spec
    if target:
        # Relocation changes only repository names, never image digests/config IDs.
        values = spec.model_dump()
        values["images"] = {
            key: dict(image.model_dump(), reference=target + "/" + key + "@" + image.digest)
            for key, image in spec.images.items()
        }
        consumed = parse(canonical(values), Spec)
    consume(consumed, tmp_path, registry)
    if target:
        # Isolated registry protocol fixtures, never publication/review evidence.
        from tests.backend.unit.test_release_manifest import manifest_data

        fixture = parse(canonical(manifest_data()), Manifest)
        repository = target + "/manifest-fixtures"
        reference = registry.publish(repository, fixture)
        assert registry.fetch(reference) == fixture
        assert registry.publish(repository, fixture) == reference
        altered = fixture.model_dump()
        altered["created_at"] = "2026-11-04T00:00:00Z"
        with pytest.raises(ReleaseError, match="release-id-conflict"):
            registry.publish(repository, parse(canonical(altered), Manifest))
        with pytest.raises(ReleaseError):
            registry.fetch(repository + "@sha256:" + "0" * 64)
    # Record the original publication repository, verified using identical OCI bytes.
    write(
        Path(os.environ["RELEASE_PROOF"]),
        proof(spec, "candidate" if stage == "candidate" else "published"),
    )
