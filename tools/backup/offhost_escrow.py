"""Keep each point's exact Docker images, backend migrations and reviewed configuration."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field
from tools.backup.offhost_contract import (
    Code,
    Image,
    RemoteError,
    Sha,
    Versioned,
    canonical,
    contract,
    private_file,
)
from tools.backup.offhost_image_archive import (
    ArchiveImage,
    exported_config_id,
    inspect_archive,
    json_value,
    platform_manifest,
    verify_vendor,
)
from tools.backup.offhost_io import Deadline, digest, directory, run
from tools.backup.offhost_package import Entry
from tools.deploy.release_images import local_image_identity
from tools.deploy.release_manifest import (
    ROOT,
    ReleaseError,
    parse,
    require_compatible,
)
from tools.deploy.release_manifest import (
    Manifest as Release,
)

from app.backup.manifest import Digest, Manifest, StrictModel, no_duplicate_keys
from app.backup.schema import migration_contract
from app.backup.storage import sync_directory, write_private


class EscrowImage(StrictModel):
    reference: str = Field(max_length=512)
    local_id: Image
    config_id: Image
    sha256: Digest
    bytes: int = Field(gt=0, le=4 * 1024**3)


class Escrow(Versioned):
    format: str = Field(pattern=r"^prl-application-escrow$")
    format_version: int = Field(ge=1, le=1)
    source_sha: Sha
    migration_sha256: Digest
    schema_revision: str = Field(max_length=64)
    release_sha256: Digest
    images: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")], EscrowImage] = Field(
        min_length=4, max_length=16
    )


def capture(args: list[str], deadline: Deadline, maximum: int = 1024**2) -> bytes:
    with tempfile.TemporaryFile() as output:
        run(args, deadline, stdout=output)
        if output.tell() > maximum:
            raise RemoteError(Code.IMAGE)
        output.seek(0)
        return output.read(maximum + 1)


def inspect(reference: str, deadline: Deadline) -> dict:
    try:
        raw = capture(
            ["docker", "image", "inspect", "--format", "{{json .}}", reference],
            deadline,
        )
        image = json.loads(raw, object_pairs_hook=no_duplicate_keys)
        return {
            "id": image["Id"],
            "descriptor": image.get("Descriptor"),
            "labels": (image.get("Config") or {}).get("Labels"),
            "os": image["Os"],
            "arch": image["Architecture"],
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RemoteError(Code.IMAGE) from None


def vendor_proof(reference: str, image: ArchiveImage, deadline: Deadline) -> bytes | None:
    pinned = reference.rsplit("@", 1)[1]
    if pinned in image.pinned_records:
        verify_vendor(reference, image, image.pinned_records)
        return None
    repository = reference.rsplit("@", 1)[0]

    def fetch(digest: str) -> bytes:
        return capture(["skopeo", "inspect", "--raw", f"docker://{repository}@{digest}"], deadline)

    try:
        top = fetch(pinned)
        records = {pinned: top}
        if "manifests" in json_value(top):
            selected = platform_manifest(json_value(top))
            records[selected.digest] = fetch(selected.digest)
        verify_vendor(reference, image, records)
        raw = canonical({key: value.decode("utf-8") for key, value in records.items()})
        if len(raw) > 1024**2:
            raise ValueError()
        return raw
    except (KeyError, TypeError, AttributeError, ValueError):
        raise RemoteError(Code.IMAGE) from None


def archived_vendor_proof(
    proof: Path, reference: str, image: ArchiveImage, *, required: bool
) -> bytes | None:
    if proof.exists() or proof.is_symlink():
        raw = private_file(proof, 1024**2)
        try:
            records = {key: value.encode("utf-8") for key, value in json_value(raw).items()}
        except (ValueError, TypeError, AttributeError):
            raise RemoteError(Code.IMAGE) from None
        verify_vendor(reference, image, records)
        return raw
    if required or reference.rsplit("@", 1)[1] in image.pinned_records:
        verify_vendor(reference, image, image.pinned_records)
    return None


def release_at(path: Path) -> Release:
    return parse_release(private_file(path, 1024**2, secret=False))


def parse_release(raw: bytes) -> Release:
    try:
        return parse(raw, Release)
    except ReleaseError:
        raise RemoteError(Code.IMAGE) from None


def prepare(release_file: Path, destination: Path, maximum: int, deadline: Deadline) -> Escrow:
    directory(destination)
    if any(destination.iterdir()):
        raise RemoteError(Code.IMAGE)
    release = release_at(release_file)
    try:
        require_compatible(release)
    except ReleaseError:
        raise RemoteError(Code.IMAGE) from None
    revision, migrations = migration_contract()
    if revision not in release.accepted_schema_revisions:
        raise RemoteError(Code.IMAGE)
    raw_release = release_file.read_bytes()
    write_private(destination / "release.json", raw_release)
    images = {}
    directory(destination / "images")
    references = {name: image.reference for name, image in release.images.items()}
    references.update(release.vendor_images)
    total = 0
    for name, reference in sorted(references.items()):
        local = inspect(reference, deadline)
        if local.get("os") != "linux" or local.get("arch") != "amd64":
            raise RemoteError(Code.IMAGE)
        if name in release.images:
            try:
                local_image_identity(release.images[name], local)
            except ReleaseError:
                raise RemoteError(Code.IMAGE) from None
            if (local.get("labels") or {}).get(
                "org.opencontainers.image.revision"
            ) != release.provenance.source_sha:
                raise RemoteError(Code.IMAGE)
        if name == "backend":
            observed = json.loads(
                capture(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges:true",
                        local["id"],
                        "python",
                        "-c",
                        "import json; from app.backup.schema import migration_contract; "
                        "print(json.dumps(migration_contract()))",
                    ],
                    deadline,
                )
            )
            if observed != [revision, migrations]:
                raise RemoteError(Code.IMAGE)
        path = destination / "images" / f"{name}.tar"
        run(["docker", "image", "save", "--output", str(path), local["id"]], deadline)
        path.chmod(0o600)
        sha, size = digest(path, maximum, deadline)
        total += size
        if total > maximum:
            raise RemoteError(Code.IMAGE)
        archived = inspect_archive(path, deadline)
        config_id = archived.config_id
        if name in release.images:
            if config_id != release.images[name].image_id:
                raise RemoteError(Code.IMAGE)
        elif (pin := vendor_proof(reference, archived, deadline)) is not None:
            write_private(destination / "images" / f"{name}.pin.json", pin)
        images[name] = dict(
            reference=reference, local_id=local["id"], config_id=config_id, sha256=sha, bytes=size
        )
    for item in release.configuration.files:
        relative = Path(item.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RemoteError(Code.IMAGE)
        raw = (ROOT / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise RemoteError(Code.IMAGE)
        path = destination / "configuration" / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(path, raw)
    result = contract(
        canonical(
            dict(
                format="prl-application-escrow",
                format_version=1,
                source_sha=release.provenance.source_sha,
                migration_sha256=migrations,
                schema_revision=revision,
                release_sha256=hashlib.sha256(raw_release).hexdigest(),
                images=images,
            )
        ),
        Escrow,
    )
    write_private(destination / "escrow.json", canonical(result))
    return result


def verify(
    path: Path,
    backup: Manifest,
    maximum: int,
    deadline: Deadline,
    *,
    require_vendor_pin: bool = True,
) -> tuple[Escrow, Release, dict[str, tuple[Path, Entry]]]:
    escrow_raw = private_file(path / "escrow.json", 1024**2)
    release_raw = private_file(path / "release.json", 1024**2)
    escrow = contract(escrow_raw, Escrow)
    release = parse_release(release_raw)
    if (
        escrow.source_sha != release.provenance.source_sha
        or escrow.source_sha != backup.source_sha
        or escrow.migration_sha256 != backup.source.migration_sha256
        or escrow.schema_revision != backup.source.revision
        or backup.source.revision not in release.accepted_schema_revisions
        or set(escrow.images) != set(release.images) | set(release.vendor_images)
        or escrow.images["backend"].local_id != backup.image_id
        or hashlib.sha256(release_raw).hexdigest() != escrow.release_sha256
    ):
        raise RemoteError(Code.IMAGE)
    files = {
        name: (path / name, Entry(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        for name, raw in (("release.json", release_raw), ("escrow.json", escrow_raw))
    }
    for name, image in escrow.images.items():
        expected = (
            release.images[name].reference
            if name in release.images
            else release.vendor_images[name]
        )
        archive = path / "images" / f"{name}.tar"
        if image.reference != expected or digest(archive, maximum, deadline) != (
            image.sha256,
            image.bytes,
        ):
            raise RemoteError(Code.IMAGE)
        archived = inspect_archive(archive, deadline)
        config_id, config = archived.config_id, archived.config
        if config_id != image.config_id or (
            name in release.images
            and (
                config_id != release.images[name].image_id
                or (config.get("config", {}).get("Labels") or {}).get(
                    "org.opencontainers.image.revision"
                )
                != backup.source_sha
            )
        ):
            raise RemoteError(Code.IMAGE)
        files[f"images/{name}.tar"] = (archive, Entry(bytes=image.bytes, sha256=image.sha256))
        if name in release.vendor_images:
            proof = path / "images" / f"{name}.pin.json"
            raw = archived_vendor_proof(proof, expected, archived, required=require_vendor_pin)
            if raw is not None:
                files[f"images/{name}.pin.json"] = (
                    proof,
                    Entry(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()),
                )
    for item in release.configuration.files:
        name = "configuration/" + item.path
        from tools.backup.offhost_package import safe_name

        if not safe_name(name):
            raise RemoteError(Code.IMAGE)
        source = path / name
        actual, size = digest(source, 1024**2 * 16, deadline)
        if actual != item.sha256:
            raise RemoteError(Code.IMAGE)
        files[name] = (source, Entry(bytes=size, sha256=item.sha256))
    return escrow, release, files


@dataclass(frozen=True)
class ImportedImage:
    runtime_id: str
    archive: ArchiveImage


def daemon_config_id(reference: str, deadline: Deadline) -> str:
    # Image inspect on containerd returns the manifest/index digest. Export the
    # selected Linux/amd64 image to verify its actual config bytes independently.
    with tempfile.TemporaryDirectory(prefix="prl-image-config-") as scratch:
        output = Path(scratch) / "image.tar"
        run(
            [
                "docker",
                "image",
                "save",
                "--platform",
                "linux/amd64",
                "--output",
                str(output),
                reference,
            ],
            deadline,
        )
        return exported_config_id(output, deadline)


def target_image(archived: ArchiveImage, deadline: Deadline) -> ImportedImage:
    descriptors = {
        item.digest: item for item in (archived.target, archived.selected) if item is not None
    }
    for candidate in (*descriptors, archived.config_id):
        try:
            observed = inspect(candidate, deadline)
        except RemoteError as error:
            if error.code == Code.TIMEOUT:
                raise
            continue
        identifier = observed.get("id")
        descriptor = observed.get("descriptor")
        if not isinstance(identifier, str) or (observed.get("os"), observed.get("arch")) != (
            "linux",
            "amd64",
        ):
            raise RemoteError(Code.IMAGE)
        if identifier == archived.config_id and descriptor is None:
            return ImportedImage(identifier, archived)
        expected = descriptors.get(identifier)
        if (
            expected is not None
            and isinstance(descriptor, dict)
            and descriptor.get("digest") == expected.digest
            and descriptor.get("size") == expected.size
            and descriptor.get("mediaType") == expected.media_type
            and daemon_config_id(identifier, deadline) == archived.config_id
        ):
            return ImportedImage(identifier, archived)
        raise RemoteError(Code.IMAGE)
    raise RemoteError(Code.IMAGE)


def import_images(path: Path, escrow: Escrow, deadline: Deadline) -> dict[str, ImportedImage]:
    imported = {}
    for name, image in escrow.images.items():
        archive = path / "images" / f"{name}.tar"
        if digest(archive, image.bytes, deadline) != (image.sha256, image.bytes):
            raise RemoteError(Code.IMAGE)
        archived = inspect_archive(archive, deadline)
        if archived.config_id != image.config_id:
            raise RemoteError(Code.IMAGE)
        run(["docker", "image", "load", "--input", str(archive)], deadline)
        imported[name] = target_image(archived, deadline)
    return imported


def image_overlay(
    escrow: Escrow, release: Release, imported: dict[str, ImportedImage] | None = None
) -> bytes:
    """Recovery consumes the verified imported IDs with no registry/build fallback."""
    if imported is None:
        resolved = {name: image.local_id for name, image in escrow.images.items()}
    else:
        if set(imported) != set(escrow.images):
            raise RemoteError(Code.IMAGE)
        resolved = {}
        for name, value in imported.items():
            known = {value.archive.config_id}
            known.update(
                item.digest
                for item in (value.archive.target, value.archive.selected)
                if item is not None
            )
            if (
                value.archive.config_id != escrow.images[name].config_id
                or value.runtime_id not in known
            ):
                raise RemoteError(Code.IMAGE)
            resolved[name] = value.runtime_id
    names = {name: resolved[key] for name, key in release.services.items()}
    names.update({name: resolved[name] for name in release.vendor_images})
    if release.configuration.observability:
        names.update(
            backup_metrics_exporter=resolved["backend"],
            gateway_log_rotate=resolved["gateway"],
        )
    lines = ["# Generated only after receipt, archive and image verification.", "services:"]
    for name, image in sorted(names.items()):
        lines += [f"  {name}:", f"    image: {image}", "    pull_policy: never"]
        if name in release.services or name == "backup_metrics_exporter":
            lines += ["    build: !reset null"]
    return ("\n".join(lines) + "\n").encode()


def materialize_configuration(
    path: Path, release: Release, target: Path, deadline: Deadline
) -> None:
    """Copy only verified nonsecret configuration for read-only mounts by service UIDs."""
    from tools.backup.offhost_package import safe_name

    if (
        not target.is_absolute()
        or target.exists()
        or target.is_symlink()
        or any(parent.is_symlink() for parent in target.parents)
        or target.resolve().is_relative_to(path.resolve())
    ):
        raise RemoteError(Code.CONFIGURATION)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".configuration-", dir=target.parent) as temporary:
        working = Path(temporary)
        if working.resolve().parent != target.parent.resolve():
            raise RemoteError(Code.CONFIGURATION)
        for item in release.configuration.files:
            name = "configuration/" + item.path
            if not safe_name(name):
                raise RemoteError(Code.IMAGE)
            source = path / name
            if digest(source, 16 * 1024**2, deadline)[0] != item.sha256:
                raise RemoteError(Code.IMAGE)
            output = working / item.path
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            write_private(output, source.read_bytes())
            output.chmod(0o644)
        # Canonical payloads stay private. Only this allowlisted public tree is
        # readable by MySQL UID999 and Nginx UID101 through read-only bind mounts.
        working.chmod(0o755)
        sync_directory(working)
        os.rename(working, target)
        sync_directory(target.parent)
