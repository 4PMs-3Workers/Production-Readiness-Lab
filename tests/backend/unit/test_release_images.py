"""Archive integrity, registry mismatches and publishing failure boundaries."""

import io
import tarfile
from pathlib import Path

import pytest
from tests.backend.unit.test_release_manifest import manifest_data, spec_data
from tools.deploy.release import publish
from tools.deploy.release_images import OCI_MANIFEST, Registry, archive_image
from tools.deploy.release_manifest import (
    Manifest,
    ReleaseError,
    Spec,
    canonical,
    parse,
    proof,
    sha256,
)
from tools.deploy.release_retention import Roots, check_roots


def make_archive(path: Path, *, source="a" * 40, architecture="amd64", corrupt=False):
    config = canonical(
        {
            "os": "linux",
            "architecture": architecture,
            "config": {"Labels": {"org.opencontainers.image.revision": source}},
        }
    )
    layer = b"a compressed test layer"

    def descriptor(raw, media):
        return {"digest": sha256(raw), "size": len(raw), "mediaType": media}

    config_desc = descriptor(config, "application/vnd.oci.image.config.v1+json")
    layer_desc = descriptor(layer, "application/vnd.oci.image.layer.v1.tar+gzip")
    manifest = canonical(
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": config_desc,
            "layers": [layer_desc],
        }
    )
    manifest_desc = descriptor(manifest, OCI_MANIFEST)
    files = {
        "oci-layout": canonical({"imageLayoutVersion": "1.0.0"}),
        "index.json": canonical({"schemaVersion": 2, "manifests": [manifest_desc]}),
        "blobs/sha256/" + sha256(config)[7:]: config,
        "blobs/sha256/" + sha256(manifest)[7:]: manifest,
        "blobs/sha256/" + sha256(layer)[7:]: b"corrupt" if corrupt else layer,
    }
    with tarfile.open(path, "w") as archive:
        for name, raw in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return manifest, config


def test_archive_checks_all_blobs_platform_and_source(tmp_path):
    path = tmp_path / "image.tar"
    manifest, config = make_archive(path)
    image = archive_image(path, "ghcr.io/example/prl/backend", "a" * 40)
    assert image.digest == sha256(manifest)
    assert image.image_id == sha256(config)
    for source, architecture, corrupt in (
        ("b" * 40, "amd64", False),
        ("a" * 40, "arm64", False),
        ("a" * 40, "amd64", True),
    ):
        make_archive(path, source=source, architecture=architecture, corrupt=corrupt)
        with pytest.raises(ReleaseError, match="invalid-image-archive"):
            archive_image(path, "ghcr.io/example/prl/backend", "a" * 40)


def test_registry_verifies_raw_bytes_config_and_label(monkeypatch, tmp_path):
    path = tmp_path / "image.tar"
    manifest, config = make_archive(path)
    image = archive_image(path, "ghcr.io/example/prl/backend", "a" * 40)
    registry = Registry()

    def inspect(*args):
        assert "--raw" in args
        return config if "--config" in args else manifest

    monkeypatch.setattr(registry, "skopeo", inspect)
    registry.verify_image(image, "a" * 40)
    with pytest.raises(ReleaseError, match="image-source-mismatch"):
        registry.verify_image(image, "b" * 40)
    monkeypatch.setattr(registry, "skopeo", lambda *args: manifest + b" ")
    with pytest.raises(ReleaseError, match="registry-digest-mismatch"):
        registry.verify_image(image, "a" * 40)


def test_plain_http_is_loopback_only():
    registry = Registry(plain_http=True)
    with pytest.raises(ReleaseError, match="unsafe-registry"):
        registry._allow("ghcr.io/example/prl/backend")


@pytest.mark.parametrize("corrupt", [False, True])
def test_complete_verification_downloads_and_checks_fresh_archives(monkeypatch, tmp_path, corrupt):
    path = tmp_path / "original.tar"
    manifest, config = make_archive(path)
    image = archive_image(path, "ghcr.io/example/prl/backend", "a" * 40)
    destinations = []
    registry = Registry()

    def skopeo(operation, *args):
        if operation == "inspect":
            return config if "--config" in args else manifest
        assert operation == "copy" and "--preserve-digests" in args
        destination = Path(args[-1].removeprefix("oci-archive:"))
        destinations.append(destination)
        make_archive(destination, corrupt=corrupt)
        return b""

    monkeypatch.setattr(registry, "skopeo", skopeo)
    for _ in range(2):
        if corrupt:
            with pytest.raises(ReleaseError, match="invalid-image-archive"):
                registry.verify_image(image, "a" * 40, complete=True)
        else:
            registry.verify_image(image, "a" * 40, complete=True)
    assert len(set(destinations)) == 2
    assert all(not destination.parent.exists() for destination in destinations)


class FakeRegistry(Registry):
    def __init__(self, *, failure=False):
        self.failure = failure
        self.committed = False
        self.visited = []
        self.complete_checks = []

    def fetch(self, reference):
        self.visited.append(reference)
        if self.failure:
            raise ReleaseError("missing-artifact")
        return parse(canonical(manifest_data()), Manifest)

    def verify_images(self, spec, *, pull=False, complete=False):
        self.complete_checks.append(complete)
        if self.failure:
            raise ReleaseError("missing-artifact")

    def publish(self, repository, manifest):
        self.committed = True
        return repository + "@sha256:" + "a" * 64


def test_retention_roots_include_previous_stable_and_backups():
    data = spec_data()
    data.update(bootstrap=False, previous_stable="ghcr.io/example/releases@sha256:" + "a" * 64)
    spec = parse(canonical(data), Spec)
    roots = Roots(format_version=1, releases=["ghcr.io/example/releases@sha256:" + "b" * 64])
    registry = FakeRegistry()
    retained = check_roots(spec, roots, registry)
    assert set(retained) == {spec.previous_stable, *roots.releases}
    assert registry.complete_checks == [True, True]
    assert not registry.committed
    with pytest.raises(ReleaseError, match="missing-artifact"):
        check_roots(spec, roots, FakeRegistry(failure=True))


@pytest.mark.parametrize("failure", ["proof", "archive", "registry", "roots"])
def test_failure_never_commits_release_manifest(monkeypatch, tmp_path, failure):
    spec = parse(canonical(spec_data()), Spec)
    candidate_proof = proof(spec, "candidate")
    registry = FakeRegistry(failure=failure == "registry")
    if failure == "proof":
        data = candidate_proof.model_dump()
        data["provenance"]["source_sha"] = "f" * 40
        candidate_proof = type(candidate_proof).model_validate(data)
    monkeypatch.setattr(
        "tools.deploy.release.archive_image",
        lambda path, repo, sha: spec.images[path.stem] if failure != "archive" else None,
    )
    if failure == "roots":
        monkeypatch.setattr(
            "tools.deploy.release.check_roots",
            lambda *args: (_ for _ in ()).throw(ReleaseError("missing-artifact")),
        )
    with pytest.raises(ReleaseError):
        publish(
            spec,
            tmp_path,
            candidate_proof,
            proof(spec, "published"),
            Roots(format_version=1, releases=[]),
            registry,
            "ghcr.io/example/releases",
            sorted(["backend", "frontend", "security", "containers", "recovery"]),
            456,
        )
    assert not registry.committed
