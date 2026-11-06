import hashlib
import io
import json
import tarfile

import pytest
from tests.backend.unit.rollout_fixtures import release
from tests.backend.unit.test_backup_manifest import sample_manifest
from tools.backup.offhost_contract import RemoteError, canonical
from tools.backup.offhost_escrow import Escrow, archive_config, verify
from tools.backup.offhost_io import Deadline
from tools.deploy.release_manifest import ROOT, Spec, parse, proof
from tools.deploy.release_manifest import Manifest as Release

from app.backup.manifest import manifest_bytes, parse_manifest


def image_archive(path, *, tags=None, source="a" * 40):
    raw = json.dumps(
        dict(
            os="linux",
            architecture="amd64",
            config=dict(Labels={"org.opencontainers.image.revision": source}),
            rootfs=dict(type="layers", diff_ids=[]),
        )
    ).encode()
    config_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    with tarfile.open(path, "w") as archive:
        for name, payload in (
            ("config.json", raw),
            (
                "manifest.json",
                json.dumps([dict(Config="config.json", RepoTags=tags, Layers=[])]).encode(),
            ),
        ):
            item = tarfile.TarInfo(name)
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
    path.chmod(0o600)
    return config_id


def escrow_fixture(tmp_path):
    data = release().model_dump(mode="json")
    images = {}
    (tmp_path / "images").mkdir(mode=0o700)
    for name in sorted(set(data["images"]) | set(data["vendor_images"])):
        path = tmp_path / "images" / f"{name}.tar"
        identity = image_archive(path)
        reference = (
            data["images"][name]["reference"]
            if name in data["images"]
            else data["vendor_images"][name]
        )
        if name in data["images"]:
            data["images"][name]["image_id"] = identity
        images[name] = dict(
            reference=reference,
            local_id=identity,
            config_id=identity,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
    candidate = {
        key: value
        for key, value in data.items()
        if key
        not in {"candidate_proof", "published_proof", "source_checks", "candidate_artifact_id"}
    }
    candidate["format"] = "prl-candidate"
    spec = parse(canonical(candidate), Spec)
    data.update(
        candidate_proof=proof(spec, "candidate").model_dump(mode="json"),
        published_proof=proof(spec, "published").model_dump(mode="json"),
    )
    selected = parse(canonical(data), Release)
    raw_release = canonical(selected.model_dump(mode="json"))
    (tmp_path / "release.json").write_bytes(raw_release)
    (tmp_path / "release.json").chmod(0o600)
    backup = sample_manifest()
    backup["image_id"] = images["backend"]["local_id"]
    backup = parse_manifest(manifest_bytes(backup))
    escrow = Escrow.model_validate(
        dict(
            format="prl-application-escrow",
            format_version=1,
            source_sha=backup.source_sha,
            schema_revision=backup.source.revision,
            migration_sha256=backup.source.migration_sha256,
            release_sha256=hashlib.sha256(raw_release).hexdigest(),
            images=images,
        )
    )
    (tmp_path / "escrow.json").write_bytes(canonical(escrow))
    (tmp_path / "escrow.json").chmod(0o600)
    for item in selected.configuration.files:
        path = tmp_path / "configuration" / item.path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes((ROOT / item.path).read_bytes())
        path.chmod(0o600)
    return escrow, backup


def test_escrow_preserves_images_migrations_release_and_configuration(tmp_path):
    expected, backup = escrow_fixture(tmp_path)
    escrow, selected, files = verify(tmp_path, backup, 1024**2, Deadline(10))
    assert escrow == expected
    assert selected.provenance.source_sha == backup.source_sha
    assert {
        "images/backend.tar",
        "images/frontend.tar",
        "images/mysql.tar",
        "images/gateway.tar",
    } <= files.keys()
    assert (
        any("migrations" in name or "alembic" in name for name in files)
        or escrow.migration_sha256 == backup.source.migration_sha256
    )


@pytest.mark.parametrize(
    "fault", ["image", "configuration", "release", "source", "migrations", "missing"]
)
def test_incompatible_or_corrupt_application_dependencies_fail_closed(tmp_path, fault):
    _, backup = escrow_fixture(tmp_path)
    if fault == "image":
        (tmp_path / "images/backend.tar").write_bytes(b"corrupt")
    elif fault == "configuration":
        next((tmp_path / "configuration").rglob("compose.yaml")).write_bytes(b"corrupt")
    elif fault == "release":
        (tmp_path / "release.json").write_bytes(b"{}")
    elif fault == "missing":
        (tmp_path / "images/frontend.tar").unlink()
    else:
        data = backup.model_dump(mode="json")
        if fault == "source":
            data["source_sha"] = "b" * 40
        else:
            data["source"]["migration_sha256"] = "b" * 64
        backup = parse_manifest(json.dumps(data).encode(), compatible=False)
    with pytest.raises((RemoteError, OSError)):
        verify(tmp_path, backup, 1024**2, Deadline(10))


def test_mutable_tagged_image_archives_are_refused_before_loading(tmp_path):
    path = tmp_path / "image.tar"
    image_archive(path, tags=["unrelated:do-not-overwrite"])
    with pytest.raises(RemoteError):
        archive_config(path, Deadline(5))
