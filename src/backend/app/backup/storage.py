"""Private, atomically completed artifacts on a separate local filesystem store."""

import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from app.backup.codec import MAX_RECORD_BYTES, decode_row
from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.manifest import (
    BACKUP_ID,
    MAX_MANIFEST_BYTES,
    Manifest,
    manifest_bytes,
    parse_manifest,
)


def store_root(config: BackupConfig, *, create: bool = False) -> Path:
    path = config.store
    if not path.is_absolute() or path.is_symlink():
        raise BackupError(Reason.STORAGE)
    root = path.resolve()
    if (
        root == Path(root.anchor)
        or root == Path("/var/lib/mysql")
        or Path("/var/lib/mysql") in root.parents
    ):
        raise BackupError(Reason.STORAGE)
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir():
        raise BackupError(Reason.STORAGE)
    if os.name == "posix" and (root.stat().st_mode & 0o077 or root.stat().st_uid != os.geteuid()):
        raise BackupError(Reason.STORAGE)
    return root


def capacity(path: Path, required: int, minimum: int) -> None:
    if shutil.disk_usage(path).free - required < minimum:
        raise BackupError(Reason.STORAGE)


def _read(path: Path, maximum: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise BackupError(Reason.INTEGRITY)
    if os.name == "posix" and info.st_mode & 0o077:
        raise BackupError(Reason.STORAGE)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise BackupError(Reason.INTEGRITY)
    return value


def write_private(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _verify(
    directory: Path, backup_id: str, config: BackupConfig, *, compatible: bool = True
) -> Manifest:
    raw = _read(directory / "manifest.json", MAX_MANIFEST_BYTES)
    if _read(directory / "COMPLETE", 64) != hashlib.sha256(raw).hexdigest().encode():
        raise BackupError(Reason.INTEGRITY)
    manifest = parse_manifest(
        raw, compatible=compatible, expected_sha=config.source_sha if compatible else None
    )
    if manifest.backup_id != backup_id:
        raise BackupError(Reason.INTEGRITY)
    expected = {"manifest.json", "COMPLETE"} | {
        f"{table.name}.jsonl" for table in manifest.source.tables
    }
    if {path.name for path in directory.iterdir()} != expected:
        raise BackupError(Reason.INTEGRITY)
    total = len(raw) + 64
    for table in manifest.source.tables:
        path = directory / f"{table.name}.jsonl"
        info, expected_file = path.lstat(), manifest.files[table.name]
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_file.bytes:
            raise BackupError(Reason.INTEGRITY)
        if os.name == "posix" and info.st_mode & 0o077:
            raise BackupError(Reason.STORAGE)
        total += info.st_size
        if total > config.max_bytes:
            raise BackupError(Reason.STORAGE)
        digest, rows = hashlib.sha256(), 0
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            while raw_row := source.readline(MAX_RECORD_BYTES + 1):
                row = decode_row(raw_row, len(table.columns))
                if table.name == "alembic_version" and row != (manifest.source.revision,):
                    raise BackupError(Reason.INTEGRITY)
                digest.update(raw_row)
                rows += 1
        if rows != expected_file.rows or digest.hexdigest() != expected_file.sha256:
            raise BackupError(Reason.INTEGRITY)
    return manifest


def load_artifact(config: BackupConfig, backup_id: str, *, compatible: bool = True) -> Manifest:
    if not BACKUP_ID.fullmatch(backup_id):
        raise BackupError(Reason.INTEGRITY)
    root = store_root(config)
    path = root / backup_id
    if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
        raise BackupError(Reason.INTEGRITY)
    try:
        return _verify(path, backup_id, config, compatible=compatible)
    except OSError:
        raise BackupError(Reason.INTEGRITY) from None


class Staging:
    def __init__(self, config: BackupConfig, backup_id: str):
        self.config, self.backup_id = config, backup_id
        self.root = store_root(config, create=True)
        if not BACKUP_ID.fullmatch(backup_id):
            raise BackupError(Reason.CONFIGURATION)
        self.path = self.root / (".partial-" + backup_id)
        self.final = self.root / backup_id
        self.published = False
        capacity(self.root, 0, config.min_free_bytes)
        if self.final.exists():
            raise BackupError(Reason.STORAGE)
        self.path.mkdir(mode=0o700)

    def publish(self, value: dict) -> Manifest:
        raw = manifest_bytes(value)
        capacity(self.root, len(raw) + 64, self.config.min_free_bytes)
        write_private(self.path / "manifest.json", raw)
        write_private(self.path / "COMPLETE", hashlib.sha256(raw).hexdigest().encode())
        manifest = _verify(self.path, self.backup_id, self.config)
        sync_directory(self.path)
        os.rename(self.path, self.final)
        try:
            sync_directory(self.root)
        except OSError:
            os.rename(self.final, self.path)
            raise
        self.published = True
        return manifest

    def cleanup(self) -> None:
        if not self.published and self.path.exists():
            # Delete only the exact private directory created by this invocation.
            if self.path.is_symlink() or self.path.resolve().parent != self.root:
                raise BackupError(Reason.STORAGE)
            shutil.rmtree(self.path)


@contextmanager
def staging(config: BackupConfig, backup_id: str):
    owned = Staging(config, backup_id)
    try:
        yield owned
    finally:
        owned.cleanup()
