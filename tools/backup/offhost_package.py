"""Stream a bounded, allowlisted private tar package; never extract arbitrary members."""

import hashlib
import os
import re
import tarfile
from pathlib import Path

from pydantic import Field, model_validator
from tools.backup.offhost_contract import (
    Code,
    RemoteError,
    Versioned,
    canonical,
    contract,
)
from tools.backup.offhost_io import Deadline, digest, directory

from app.backup.manifest import BACKUP_ID, Digest, StrictModel
from app.backup.storage import capacity, sync_directory


def safe_name(name: str) -> bool:
    return bool(
        name in {"release.json", "escrow.json", "images.tar"}
        or re.fullmatch(r"images/[a-z][a-z0-9_]{0,31}\.tar", name)
        or re.fullmatch(r"backup/(?:manifest\.json|COMPLETE|[A-Za-z][A-Za-z0-9_]*\.jsonl)", name)
        or (
            re.fullmatch(r"configuration/(?:tools|src|Dockerfile)[A-Za-z0-9_./-]*", name)
            and all(part not in {"", ".", ".."} for part in name.split("/"))
        )
    )


class Entry(StrictModel):
    bytes: int = Field(ge=0, le=4 * 1024**3)
    sha256: Digest


class BoundedReader:
    def __init__(self, source, deadline: Deadline):
        self.source, self.deadline = source, deadline

    def read(self, size=-1):
        self.deadline.remaining()
        return self.source.read(size)


class Package(Versioned):
    format: str = Field(pattern=r"^prl-offhost-package$")
    format_version: int = Field(ge=1, le=1)
    backup_id: str
    files: dict[str, Entry] = Field(min_length=6, max_length=256)

    @model_validator(mode="after")
    def coherent(self):
        if (
            not BACKUP_ID.fullmatch(self.backup_id)
            or not {
                "backup/manifest.json",
                "backup/COMPLETE",
                "release.json",
                "escrow.json",
            }
            <= self.files.keys()
            or not all(safe_name(name) for name in self.files)
        ):
            raise ValueError()
        return self


def pack(
    files: dict[str, Path], backup_id: str, output: Path, maximum: int, deadline: Deadline
) -> Package:
    entries = {}
    for name, path in files.items():
        sha, size = digest(path, maximum, deadline)
        entries[name] = dict(bytes=size, sha256=sha)
    metadata = contract(
        canonical(
            dict(
                format="prl-offhost-package",
                format_version=1,
                backup_id=backup_id,
                files=entries,
            )
        ),
        Package,
    )
    if sum(entry.bytes for entry in metadata.files.values()) > maximum:
        raise RemoteError(Code.INTEGRITY)
    capacity(output.parent, sum(entry.bytes for entry in metadata.files.values()), 0)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    import io

    with (
        os.fdopen(fd, "wb") as raw,
        tarfile.open(fileobj=raw, mode="w:gz", compresslevel=1) as archive,
    ):
        data = canonical(metadata)
        info = tarfile.TarInfo("package.json")
        info.size, info.mode = len(data), 0o600
        archive.addfile(info, io.BytesIO(data))
        for name, path in sorted(files.items()):
            deadline.remaining()
            entry = metadata.files[name]
            info = tarfile.TarInfo(name)
            info.size, info.mode = entry.bytes, 0o600
            with path.open("rb") as source:
                archive.addfile(info, BoundedReader(source, deadline))
            # Detect a cooperative write or input race during packaging.
            if digest(path, maximum, deadline) != (entry.sha256, entry.bytes):
                raise RemoteError(Code.INTEGRITY)
    digest(output, maximum + 8 * 1024**2, deadline)
    return metadata


def unpack(source: Path, destination: Path, maximum: int, deadline: Deadline) -> Package:
    directory(destination)
    if any(destination.iterdir()):
        raise RemoteError(Code.INTEGRITY)
    try:
        with tarfile.open(source, mode="r|gz") as archive:
            members = iter(archive)
            first = next(members, None)
            if (
                first is None
                or first.name != "package.json"
                or not first.isfile()
                or first.size > 1024**2
            ):
                raise RemoteError(Code.INTEGRITY)
            handle = archive.extractfile(first)
            if handle is None:
                raise RemoteError(Code.INTEGRITY)
            with handle:
                metadata = contract(handle.read(1024**2 + 1), Package)
            if sum(entry.bytes for entry in metadata.files.values()) > maximum:
                raise RemoteError(Code.INTEGRITY)
            capacity(destination, sum(entry.bytes for entry in metadata.files.values()), 0)
            names = []
            for member in members:
                deadline.remaining()
                name = member.name
                if name in names or name not in metadata.files or not member.isfile():
                    raise RemoteError(Code.INTEGRITY)
                entry = metadata.files[name]
                if member.size != entry.bytes:
                    raise RemoteError(Code.INTEGRITY)
                target = destination / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                handle = archive.extractfile(member)
                if handle is None:
                    raise RemoteError(Code.INTEGRITY)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                sha, count = hashlib.sha256(), 0
                with handle, os.fdopen(fd, "wb") as output:
                    while block := handle.read(1024 * 1024):
                        deadline.remaining()
                        count += len(block)
                        if count > entry.bytes:
                            raise RemoteError(Code.INTEGRITY)
                        sha.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if count != entry.bytes or sha.hexdigest() != entry.sha256:
                    raise RemoteError(Code.INTEGRITY)
                names.append(name)
            if set(names) != set(metadata.files):
                raise RemoteError(Code.INTEGRITY)
            sync_directory(destination)
            return metadata
    except (tarfile.TarError, OSError, EOFError):
        raise RemoteError(Code.INTEGRITY) from None
