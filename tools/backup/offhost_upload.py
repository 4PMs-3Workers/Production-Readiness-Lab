"""Verify local inputs and remote bytes before the sole completion publication."""

import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tools.backup.offhost_contract import (
    Code,
    Completion,
    RemoteError,
    blob_key,
    canonical,
    contract,
    payload_key,
    point_key,
    source_identity,
)
from tools.backup.offhost_escrow import verify
from tools.backup.offhost_io import age, digest, directory
from tools.backup.offhost_package import pack
from tools.backup.offhost_s3 import S3

from app.backup.config import BackupConfig
from app.backup.locking import operation_lock
from app.backup.storage import load_artifact


def read_point(remote: S3, backup_id: str) -> tuple[Completion, str] | None:
    existing = remote.get(point_key(remote.config, backup_id))
    if existing is None:
        return None
    point = contract(existing[0], Completion)
    point.require(remote.config, backup_id)
    return point, existing[1]


def verify_remote(remote: S3, point: Completion, scratch: Path) -> None:
    target = scratch / "remote-readback.age"
    try:
        remote.download(
            payload_key(remote.config, point), target, point.encrypted_sha256, point.encrypted_bytes
        )
    finally:
        target.unlink(missing_ok=True)


def upload(
    local: BackupConfig,
    remote: S3,
    backup_id: str,
    escrow_path: Path,
    executable: Path,
    scratch: Path,
) -> Completion:
    # Reuse the snapshot/retention lease so local inputs cannot be retired mid-transfer.
    with operation_lock(local):
        return upload_locked(local, remote, backup_id, escrow_path, executable, scratch)


def upload_locked(
    local: BackupConfig,
    remote: S3,
    backup_id: str,
    escrow_path: Path,
    executable: Path,
    scratch: Path,
) -> Completion:
    config, deadline = remote.config, remote.deadline
    manifest = load_artifact(local, backup_id, deadline=deadline.expires)
    if source_identity(manifest) != config.source_id:
        raise RemoteError(Code.IDENTITY)
    escrow, _, files = verify(escrow_path, manifest, config.max_bytes, deadline)
    artifact = local.store / backup_id
    raw = (artifact / "manifest.json").read_bytes()
    manifest_sha = hashlib.sha256(raw).hexdigest()
    files.update({f"backup/{path.name}": path for path in artifact.iterdir()})

    def equivalent(point: Completion) -> None:
        if (
            point.backup_manifest_sha256 != manifest_sha
            or point.release_sha256 != escrow.release_sha256
            or point.source_sha != manifest.source_sha
            or point.backend_image_id != manifest.image_id
        ):
            raise RemoteError(Code.CONFLICT)

    directory(scratch)
    with tempfile.TemporaryDirectory(prefix="upload-", dir=scratch) as temporary:
        work = Path(temporary)
        existing = read_point(remote, backup_id)
        if existing is not None:
            equivalent(existing[0])
            verify_remote(remote, existing[0], work)
            return existing[0]
        package, encrypted = work / "package.tar.gz", work / "payload.age"
        pack(files, backup_id, package, config.max_bytes, deadline)
        package_sha, _ = digest(package, config.max_bytes + 8 * 1024**2, deadline)
        age(executable, package, encrypted, deadline, recipient=config.recipient)
        encrypted_sha, encrypted_size = digest(encrypted, config.max_bytes + 16 * 1024**2, deadline)
        key = blob_key(config, backup_id, encrypted_sha)
        try:
            remote.upload(key, encrypted, encrypted_sha, encrypted_size)
        except RemoteError as error:
            if error.code != Code.CONFLICT:
                raise
            # A lost acknowledgement is safe: verify the immutable existing bytes.
        remote.download(key, work / "verified.age", encrypted_sha, encrypted_size)
        verified_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        point = contract(
            canonical(
                dict(
                    format="prl-offhost-point",
                    format_version=1,
                    state="complete",
                    backup_id=backup_id,
                    source_id=config.source_id,
                    configuration_sha256=config.identity,
                    source_sha=manifest.source_sha,
                    backend_image_id=manifest.image_id,
                    backup_manifest_sha256=manifest_sha,
                    release_sha256=escrow.release_sha256,
                    bundle_sha256=package_sha,
                    encrypted_sha256=encrypted_sha,
                    encrypted_bytes=encrypted_size,
                    snapshot=manifest.snapshot.model_dump(),
                    backup_completed_at=manifest.completed_at,
                    verified_at=verified_at,
                )
            ),
            Completion,
        )
        try:
            remote.put(point_key(config, backup_id), canonical(point))
        except RemoteError as error:
            if error.code != Code.CONFLICT:
                raise
        published = read_point(remote, backup_id)
        if published is None:
            raise RemoteError(Code.INTEGRITY)
        equivalent(published[0])
        verify_remote(remote, published[0], work)
        return published[0]
