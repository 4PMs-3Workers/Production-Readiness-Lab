"""Keep each point's exact Docker images, backend migrations and reviewed configuration."""

import hashlib
import json
import tarfile
import tempfile
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
from tools.backup.offhost_io import Deadline, digest, directory, run
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
from app.backup.storage import write_private


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
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{"id":{{json .Id}},"descriptor":{{json (index . "Descriptor")}},'
                '"labels":{{json .Config.Labels}},"os":{{json .Os}},"arch":{{json .Architecture}}}',
                reference,
            ],
            deadline,
        )
        return json.loads(raw, object_pairs_hook=no_duplicate_keys)
    except (ValueError, TypeError):
        raise RemoteError(Code.IMAGE) from None


def archive_config(path: Path, deadline: Deadline) -> tuple[str, dict]:
    """Inspect without extraction; ID-only saves must not carry mutable RepoTags."""
    try:
        with tarfile.open(path, "r:*") as archive:
            members = {}
            for item in archive:
                deadline.remaining()
                if (
                    item.name in members
                    or len(members) >= 20000
                    or item.name.startswith("/")
                    or ".." in item.name.split("/")
                    or not (item.isfile() or item.isdir())
                ):
                    raise ValueError()
                members[item.name] = item

            def read(name):
                item = members[name]
                if not item.isfile() or item.size > 1024**2:
                    raise ValueError()
                stream = archive.extractfile(item)
                if stream is None:
                    raise ValueError()
                with stream:
                    return stream.read(1024**2 + 1)

            manifest = json.loads(read("manifest.json"), object_pairs_hook=no_duplicate_keys)
            if len(manifest) != 1 or manifest[0].get("RepoTags") not in (None, []):
                raise ValueError()
            config_raw = read(manifest[0]["Config"])
            config = json.loads(config_raw, object_pairs_hook=no_duplicate_keys)
            if config.get("os") != "linux" or config.get("architecture") != "amd64":
                raise ValueError()
            for name in manifest[0]["Layers"]:
                if name not in members or not members[name].isfile():
                    raise ValueError()
            return "sha256:" + hashlib.sha256(config_raw).hexdigest(), config
    except (tarfile.TarError, OSError, ValueError, KeyError, TypeError):
        raise RemoteError(Code.IMAGE) from None


def release_at(path: Path) -> Release:
    try:
        return parse(private_file(path, 1024**2, secret=False), Release)
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
        config_id, _ = archive_config(path, deadline)
        if name in release.images and config_id != release.images[name].image_id:
            raise RemoteError(Code.IMAGE)
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
    path: Path, backup: Manifest, maximum: int, deadline: Deadline
) -> tuple[Escrow, Release, dict[str, Path]]:
    escrow = contract(private_file(path / "escrow.json", 1024**2), Escrow)
    release = release_at(path / "release.json")
    if (
        escrow.source_sha != release.provenance.source_sha
        or escrow.source_sha != backup.source_sha
        or escrow.migration_sha256 != backup.source.migration_sha256
        or escrow.schema_revision != backup.source.revision
        or backup.source.revision not in release.accepted_schema_revisions
        or set(escrow.images) != set(release.images) | set(release.vendor_images)
        or escrow.images["backend"].local_id != backup.image_id
        or hashlib.sha256((path / "release.json").read_bytes()).hexdigest() != escrow.release_sha256
    ):
        raise RemoteError(Code.IMAGE)
    files = {name: path / name for name in ("release.json", "escrow.json")}
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
        config_id, config = archive_config(archive, deadline)
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
        files[f"images/{name}.tar"] = archive
    for item in release.configuration.files:
        name = "configuration/" + item.path
        from tools.backup.offhost_package import safe_name

        if not safe_name(name):
            raise RemoteError(Code.IMAGE)
        source = path / name
        if digest(source, 1024**2 * 16, deadline)[0] != item.sha256:
            raise RemoteError(Code.IMAGE)
        files[name] = source
    return escrow, release, files


def import_images(path: Path, escrow: Escrow, deadline: Deadline) -> None:
    for name, image in escrow.images.items():
        archive = path / "images" / f"{name}.tar"
        if digest(archive, image.bytes, deadline) != (image.sha256, image.bytes):
            raise RemoteError(Code.IMAGE)
        if archive_config(archive, deadline)[0] != image.config_id:
            raise RemoteError(Code.IMAGE)
        run(["docker", "image", "load", "--input", str(archive)], deadline)
        # No registry/network fallback is permitted during import.
        observed = inspect(image.local_id, deadline)
        if observed.get("id") != image.local_id:
            raise RemoteError(Code.IMAGE)
