"""Remote-only retrieval into private staging and the existing local restore format."""

import hashlib
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from tools.backup.offhost_contract import (
    Code,
    Completion,
    RemoteError,
    canonical,
    payload_key,
    private_file,
    source_identity,
)
from tools.backup.offhost_escrow import verify
from tools.backup.offhost_io import age, digest, directory
from tools.backup.offhost_package import unpack
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import read_point

from app.backup.config import BackupConfig
from app.backup.locking import operation_lock
from app.backup.storage import load_artifact, store_root, sync_directory, write_private


def retrieve(
    target: BackupConfig,
    remote: S3,
    backup_id: str,
    executable: Path,
    identity: Path,
    *,
    temporary_root: Path | None = None,
) -> tuple[Completion, Path]:
    with operation_lock(target):
        return retrieve_locked(
            target, remote, backup_id, executable, identity, temporary_root=temporary_root
        )


def retrieve_locked(
    target: BackupConfig,
    remote: S3,
    backup_id: str,
    executable: Path,
    identity: Path,
    *,
    temporary_root: Path | None = None,
) -> tuple[Completion, Path]:
    config, deadline = remote.config, remote.deadline
    # No local artifact is considered until a remote completion and payload succeed.
    published = read_point(remote, backup_id)
    if published is None:
        raise RemoteError(Code.INTEGRITY)
    point = published[0]
    root = store_root(target, create=True)
    with tempfile.TemporaryDirectory(
        prefix=".partial-offhost-", dir=temporary_root or root
    ) as temporary:
        work = Path(temporary)
        encrypted, package = work / "payload.age", work / "package.tar.gz"
        remote.download(
            payload_key(config, point), encrypted, point.encrypted_sha256, point.encrypted_bytes
        )
        age(executable, encrypted, package, deadline, identity=identity)
        if digest(package, config.max_bytes + 8 * 1024**2, deadline)[0] != point.bundle_sha256:
            raise RemoteError(Code.INTEGRITY)
        decoded = work / "decoded"
        metadata = unpack(package, decoded, config.max_bytes, deadline)
        if metadata.backup_id != backup_id:
            raise RemoteError(Code.IDENTITY)
        validation = directory(work / "validation")
        os.rename(decoded / "backup", validation / backup_id)
        candidate = replace(target, store=validation, source_sha=point.source_sha)
        # The receipt binds the archived release. Runtime compatibility is checked
        # later by restore inside the recovered backend, using that release's schema.
        manifest = load_artifact(candidate, backup_id, compatible=False, deadline=deadline.expires)
        artifact = validation / backup_id
        if (
            source_identity(manifest) != config.source_id
            or manifest.image_id != point.backend_image_id
            or hashlib.sha256((artifact / "manifest.json").read_bytes()).hexdigest()
            != point.backup_manifest_sha256
            or manifest.snapshot != point.snapshot
            or manifest.completed_at != point.backup_completed_at
        ):
            raise RemoteError(Code.IDENTITY)
        escrow, _, files = verify(
            decoded,
            manifest,
            config.max_bytes,
            deadline,
            require_vendor_pin=point.format_version == 2,
        )
        expected = set(files) | {"backup/" + path.name for path in artifact.iterdir()}
        if set(metadata.files) != expected or escrow.release_sha256 != point.release_sha256:
            raise RemoteError(Code.INTEGRITY)

        # Escrow preparation is non-authoritative. A failed backup rename can be
        # retried against its identical immutable receipt; no prior success is removed.
        escrow_root = directory(root / ".offhost-escrow")
        escrow_target = escrow_root / backup_id
        if escrow_target.exists() or escrow_target.is_symlink():
            if escrow_target.is_symlink() or private_file(
                escrow_target / "POINT.json", 1024**2
            ) != canonical(point):
                raise RemoteError(Code.CONFLICT)
            verify(
                escrow_target,
                manifest,
                config.max_bytes,
                deadline,
                require_vendor_pin=point.format_version == 2,
            )
        else:
            write_private(decoded / "POINT.json", canonical(point))
            os.rename(decoded, escrow_target)
            sync_directory(escrow_root)
        final = root / backup_id
        if final.exists() or final.is_symlink():
            existing = load_artifact(
                replace(target, source_sha=point.source_sha),
                backup_id,
                compatible=False,
                deadline=deadline.expires,
            )
            if existing != manifest:
                raise RemoteError(Code.CONFLICT)
        else:
            deadline.remaining()
            os.rename(artifact, final)
            sync_directory(root)
        return point, escrow_target
