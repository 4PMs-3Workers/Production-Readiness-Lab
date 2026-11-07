"""Stream a bounded, allowlisted private tar package; never extract arbitrary members."""

import gzip
import hashlib
import io
import os
import re
import tarfile
from pathlib import Path
from typing import Any, BinaryIO, cast

from pydantic import Field, model_validator
from tools.backup.offhost_contract import (
    Code,
    RemoteError,
    Versioned,
    canonical,
    contract,
)
from tools.backup.offhost_io import Deadline, directory, regular

from app.backup.manifest import BACKUP_ID, Digest, StrictModel
from app.backup.storage import capacity, sync_directory

MAX_METADATA = 1024**2
MAX_FILES = 256


class PlainTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # TarInfo dispatches here after its fixed header and before extension bodies.
        if self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            raise RemoteError(Code.INTEGRITY)
        return cast(Any, super())._proc_member(archive)


def safe_name(name: str) -> bool:
    return bool(
        name in {"release.json", "escrow.json"}
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


class HashingReader(BoundedReader):
    def __init__(self, source, deadline: Deadline):
        super().__init__(source, deadline)
        self.checksum = hashlib.sha256()

    def read(self, size=-1):
        block = super().read(size)
        self.checksum.update(block)
        return block


class ArchiveReader(BoundedReader):
    def __init__(self, source, deadline: Deadline, maximum: int):
        super().__init__(source, deadline)
        self.remaining = maximum

    def read(self, size=-1):
        limit = self.remaining + 1
        data = super().read(limit if size < 0 else min(size, limit))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise RemoteError(Code.INTEGRITY)
        return data


class Package(Versioned):
    format: str = Field(pattern=r"^prl-offhost-package$")
    format_version: int = Field(ge=1, le=1)
    backup_id: str
    files: dict[str, Entry] = Field(min_length=6, max_length=MAX_FILES)

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
    files: dict[str, tuple[Path, Entry]],
    backup_id: str,
    output: Path,
    maximum: int,
    deadline: Deadline,
) -> Package:
    metadata = contract(
        canonical(
            dict(
                format="prl-offhost-package",
                format_version=1,
                backup_id=backup_id,
                files={name: entry.model_dump() for name, (_, entry) in files.items()},
            )
        ),
        Package,
    )
    if sum(entry.bytes for entry in metadata.files.values()) > maximum:
        raise RemoteError(Code.INTEGRITY)
    capacity(output.parent, sum(entry.bytes for entry in metadata.files.values()), 0)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    with (
        os.fdopen(fd, "wb") as raw,
        tarfile.open(
            fileobj=raw, mode="w:gz", format=tarfile.USTAR_FORMAT, compresslevel=1
        ) as archive,
    ):
        data = canonical(metadata)
        info = tarfile.TarInfo("package.json")
        info.size, info.mode = len(data), 0o600
        archive.addfile(info, io.BytesIO(data))
        for name, (path, entry) in sorted(files.items()):
            deadline.remaining()
            if regular(path, maximum) != entry.bytes:
                raise RemoteError(Code.INTEGRITY)
            info = tarfile.TarInfo(name)
            info.size, info.mode = entry.bytes, 0o600
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as source:
                reader = HashingReader(source, deadline)
                archive.addfile(info, reader)
                if reader.checksum.hexdigest() != entry.sha256 or source.read(1):
                    raise RemoteError(Code.INTEGRITY)
    return metadata


def unpack(source: Path, destination: Path, maximum: int, deadline: Deadline) -> Package:
    directory(destination)
    if any(destination.iterdir()):
        raise RemoteError(Code.INTEGRITY)
    try:
        with gzip.open(source, "rb") as compressed:
            return unpack_tar(compressed, destination, maximum, deadline)
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise RemoteError(Code.INTEGRITY) from None


def unpack_tar(compressed, destination: Path, maximum: int, deadline: Deadline) -> Package:
    # Budget the file payloads, inventory, headers/padding and final tar record.
    bound = maximum + MAX_METADATA + (MAX_FILES + 1) * 1024 + tarfile.RECORDSIZE
    reader = ArchiveReader(compressed, deadline, bound)
    # tarfile's streaming mode uses only read() on the supplied object.
    with tarfile.open(fileobj=cast(BinaryIO, reader), mode="r|", tarinfo=PlainTarInfo) as archive:
        members = iter(archive)
        first = next(members, None)
        if (
            first is None
            or first.name != "package.json"
            or not first.isfile()
            or first.size > MAX_METADATA
        ):
            raise RemoteError(Code.INTEGRITY)
        handle = archive.extractfile(first)
        if handle is None:
            raise RemoteError(Code.INTEGRITY)
        with handle:
            metadata = contract(handle.read(MAX_METADATA + 1), Package)
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
    while reader.read(64 * 1024):
        pass
    sync_directory(destination)
    return metadata
