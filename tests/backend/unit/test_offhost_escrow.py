import hashlib
import io
import json
import os
import tarfile
from dataclasses import replace

import pytest
from tests.backend.unit.rollout_fixtures import release
from tests.backend.unit.test_backup_manifest import sample_manifest
from tools.backup.offhost_contract import Code, RemoteError, canonical
from tools.backup.offhost_escrow import (
    Escrow,
    image_overlay,
    import_images,
    materialize_configuration,
    release_at,
    target_image,
    verify,
)
from tools.backup.offhost_image_archive import OCI, inspect_archive, sha
from tools.backup.offhost_io import Deadline
from tools.deploy.release_manifest import ROOT, Spec, parse, proof
from tools.deploy.release_manifest import Manifest as Release

from app.backup.manifest import manifest_bytes, parse_manifest


def image_archive(path, *, tags=None, source="a" * 40, oci_names=None):
    raw = json.dumps(
        dict(
            os="linux",
            architecture="amd64",
            config=dict(Labels={"org.opencontainers.image.revision": source}),
            rootfs=dict(type="layers", diff_ids=[]),
        )
    ).encode()
    config_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    records = {"config.json": raw}
    if oci_names is not None:
        manifest = vendor_manifest(config_id, len(raw))
        records.update(
            {
                "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
                "index.json": canonical(
                    dict(
                        schemaVersion=2,
                        manifests=[
                            dict(
                                digest=sha(manifest),
                                size=len(manifest),
                                mediaType=OCI + "manifest.v1+json",
                                annotations=oci_names,
                            )
                        ],
                    )
                ),
                "blobs/sha256/" + config_id[7:]: raw,
                "blobs/sha256/" + sha(manifest)[7:]: manifest,
            }
        )
    records["manifest.json"] = json.dumps(
        [dict(Config="config.json", RepoTags=tags, Layers=[])]
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, payload in records.items():
            item = tarfile.TarInfo(name)
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
    path.chmod(0o600)
    return config_id


def vendor_manifest(config_id, size):
    return canonical(
        dict(
            schemaVersion=2,
            mediaType=OCI + "manifest.v1+json",
            config=dict(digest=config_id, size=size, mediaType=OCI + "config.v1+json"),
            layers=[],
        )
    )


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
        else:
            with tarfile.open(path, "r") as saved:
                pin = vendor_manifest(identity, saved.getmember("config.json").size)
            reference = reference.rsplit("@", 1)[0] + "@" + sha(pin)
            data["vendor_images"][name] = reference
            proof_path = path.with_name(f"{name}.pin.json")
            proof_path.write_bytes(canonical({sha(pin): pin.decode()}))
            proof_path.chmod(0o600)
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
        inspect_archive(path, Deadline(5))


@pytest.mark.parametrize(
    "annotation", ["org.opencontainers.image.ref.name", "io.containerd.image.name"]
)
def test_oci_index_cannot_apply_a_tag_missing_from_legacy_manifest(tmp_path, annotation):
    path = tmp_path / "tagged-oci.tar"
    image_archive(path, oci_names={annotation: "unrelated:do-not-overwrite"})
    with pytest.raises(RemoteError):
        inspect_archive(path, Deadline(5))


def test_vendor_pin_rejects_a_substituted_archive_even_if_escrow_hashes_match(tmp_path):
    escrow, backup = escrow_fixture(tmp_path)
    path = tmp_path / "images/mysql.tar"
    substituted = image_archive(path, source="b" * 40)
    image = escrow.images["mysql"].model_copy(
        update=dict(
            config_id=substituted,
            local_id=substituted,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
    )
    changed = escrow.model_copy(update={"images": dict(escrow.images, mysql=image)})
    (tmp_path / "escrow.json").write_bytes(canonical(changed))
    with pytest.raises(RemoteError, match=Code.IMAGE.value):
        verify(tmp_path, backup, 1024**2, Deadline(5))


def test_import_resolves_config_identity_without_accepting_a_different_image(tmp_path, monkeypatch):
    escrow, _ = escrow_fixture(tmp_path)
    data = escrow.model_dump(mode="json")
    for index, image in enumerate(data["images"].values()):
        image["local_id"] = "sha256:" + f"{index + 1:064x}"
    escrow = Escrow.model_validate_json(json.dumps(data))
    configs = {image.config_id for image in escrow.images.values()}
    calls = []

    def lookup(reference, deadline):
        if reference not in configs:
            raise RemoteError(Code.IMAGE)
        return dict(id=reference, os="linux", arch="amd64")

    monkeypatch.setattr("tools.backup.offhost_escrow.inspect", lookup)
    monkeypatch.setattr(
        "tools.backup.offhost_escrow.run", lambda args, deadline: calls.append(args)
    )
    imported = import_images(tmp_path, escrow, Deadline(5))
    assert len(calls) == 4 and {value.runtime_id for value in imported.values()} == configs
    selected = release_at(tmp_path / "release.json")
    assert b"pull_policy: never" in image_overlay(escrow, selected, imported)
    imported["backend"] = replace(imported["backend"], runtime_id="sha256:" + "f" * 64)
    with pytest.raises(RemoteError):
        image_overlay(escrow, selected, imported)


def test_containerd_id_must_match_the_verified_archive_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "portable.tar"
    config = image_archive(path, oci_names={})
    image = inspect_archive(path, Deadline(5))
    descriptor = image.target
    assert descriptor is not None and config != descriptor.digest
    descriptor_fields = dict(
        digest=descriptor.digest, size=descriptor.size, mediaType=descriptor.media_type
    )
    observed = dict(
        id=descriptor.digest,
        os="linux",
        arch="amd64",
        descriptor=descriptor_fields,
    )
    monkeypatch.setattr("tools.backup.offhost_escrow.inspect", lambda *_: observed)
    assert target_image(image, Deadline(5)).runtime_id == descriptor.digest
    descriptor_fields["digest"] = "sha256:" + "f" * 64
    with pytest.raises(RemoteError, match=Code.IMAGE.value):
        target_image(image, Deadline(5))


def test_runtime_configuration_copy_is_public_allowlist_and_leaves_escrow_private(tmp_path):
    source, target = tmp_path / "escrow", tmp_path / "runtime"
    source.mkdir(mode=0o700)
    escrow_fixture(source)
    selected = release_at(source / "release.json")
    secret = source / "private-credentials.env"
    secret.write_text("private-do-not-copy", encoding="utf-8")
    original = source / "configuration/tools/deploy/mysql/10-identities.sh"
    raw = original.read_bytes()
    materialize_configuration(source, selected, target, Deadline(10))
    public = target / "tools/deploy/mysql/10-identities.sh"
    assert public.read_bytes() == raw
    assert not (target / secret.name).exists()
    if os.name != "nt":
        assert original.stat().st_mode & 0o077 == 0
        assert public.stat().st_mode & 0o777 == 0o644
        assert public.parent.stat().st_mode & 0o777 == 0o755
    with pytest.raises(RemoteError):
        materialize_configuration(source, selected, target, Deadline(10))
    assert public.read_bytes() == raw
