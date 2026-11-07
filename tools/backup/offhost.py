"""Bounded off-host operations. Credentials and age identities are private file inputs."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, NoReturn

from pydantic import Field
from tools.backup.offhost_contract import (
    Code,
    Completion,
    Digest,
    Image,
    RemoteError,
    Sha,
    Versioned,
    contract,
    load_config,
    private_file,
    source_identity,
)
from tools.backup.offhost_escrow import (
    image_overlay,
    import_images,
    materialize_configuration,
    prepare,
    verify,
)
from tools.backup.offhost_io import Deadline, directory
from tools.backup.offhost_retention import inventory, retain
from tools.backup.offhost_retrieve import retrieve
from tools.backup.offhost_s3 import S3
from tools.backup.offhost_upload import upload, verify_remote

from app.backup.config import BackupConfig, BackupError
from app.backup.monitoring import record_remote_copy
from app.backup.storage import load_artifact, write_private

OPERATIONS = ("prepare", "upload", "retrieve", "retain", "status", "import-images")


class Report(Versioned):
    format_version: Literal[1] = 1
    operation: Literal[
        "prepare", "upload", "retrieve", "retain", "status", "import-images", "configuration"
    ]
    success: bool
    reason: Code | None = None
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    backup_id: str | None = Field(default=None, pattern=r"^prl-backup-\d{8}T\d{6}Z-[0-9a-f]{32}$")
    source_sha: Sha | None = None
    backend_image_id: Image | None = None
    configuration_sha256: Digest | None = None
    backup_manifest_sha256: Digest | None = None
    release_sha256: Digest | None = None
    encrypted_sha256: Digest | None = None
    remote_copy_status: Literal["unknown", "pending", "success", "failure"] | None = None
    remote_copy_verified: bool | None = None
    artifact_integrity_verified: bool | None = None
    application_rehearsal_verified: Literal[False] = False
    removed_count: int | None = Field(default=None, ge=0)
    preserved_count: int | None = Field(default=None, ge=0)
    ignored_entries: int | None = Field(default=None, ge=0)
    point_count: int | None = Field(default=None, ge=0)
    images_imported: int | None = Field(default=None, ge=0)
    imported_image_ids: (
        dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")], Image] | None
    ) = Field(default=None, max_length=16)
    recovery_overlay_written: bool | None = None
    recovery_configuration_written: bool | None = None


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise RemoteError(Code.CONFIGURATION)


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    for flag, variable in (
        ("config", "PRL_OFFHOST_CONFIG"),
        ("credentials", "PRL_OFFHOST_CREDENTIALS"),
        ("identity", "PRL_OFFHOST_IDENTITY"),
        ("age", "PRL_OFFHOST_AGE"),
        ("escrow", "PRL_OFFHOST_ESCROW"),
        ("local-store", "PRL_OFFHOST_LOCAL_STORE"),
    ):
        parser.add_argument("--" + flag, type=Path, default=os.environ.get(variable))
    parser.add_argument("--release", type=Path)
    parser.add_argument("--backup-id")
    parser.add_argument("--output-overlay", type=Path)
    parser.add_argument("--output-configuration", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.config is None or args.local_store is None or not args.local_store.is_absolute():
        raise RemoteError(Code.CONFIGURATION)
    if (args.operation in {"upload", "retrieve", "import-images"}) != bool(args.backup_id):
        raise RemoteError(Code.CONFIGURATION)
    if args.operation in {"prepare", "upload"} and (
        args.escrow is None or not args.escrow.is_absolute()
    ):
        raise RemoteError(Code.CONFIGURATION)
    if args.operation == "prepare" and args.release is None:
        raise RemoteError(Code.CONFIGURATION)
    if args.operation in {"upload", "retrieve"} and args.age is None:
        raise RemoteError(Code.KEY)
    if args.operation == "retrieve" and args.identity is None:
        raise RemoteError(Code.KEY)
    if args.output_overlay is not None and (
        args.operation != "import-images" or not args.output_overlay.is_absolute()
    ):
        raise RemoteError(Code.CONFIGURATION)
    if args.output_configuration is not None and (
        args.operation != "import-images"
        or not args.output_configuration.is_absolute()
        or args.output_configuration.resolve().is_relative_to(args.local_store.resolve())
    ):
        raise RemoteError(Code.CONFIGURATION)
    return args


def local_config(args, config) -> BackupConfig:
    # This identity-only URL is never connected. Retrieval needs no source DB credentials.
    return BackupConfig(
        "mysql+asyncmy://offline@source-unavailable/prl",
        args.local_store,
        max_seconds=config.max_seconds,
        max_bytes=config.max_bytes,
        min_free_bytes=config.min_free_bytes,
    )


def telemetry(local, config, started, status, point: Completion | None = None):
    record_remote_copy(
        local,
        environment=config.environment,
        store=config.store,
        configuration_sha256=config.identity,
        started_at=started,
        status=status,
        verified_at=datetime.fromisoformat(point.verified_at) if point else None,
        recovery_point_at=(
            datetime.fromisoformat(point.snapshot.established_before) if point else None
        ),
    )


def perform(args, config, work: Path, started: datetime) -> dict:
    deadline, local = Deadline(config.max_seconds), local_config(args, config)
    if args.operation == "prepare":
        staged = work / "prepared"
        escrow = prepare(args.release, staged, config.max_bytes, deadline)
        if args.escrow.exists() or args.escrow.is_symlink():
            raise RemoteError(Code.CONFLICT)
        os.rename(staged, args.escrow)
        return dict(source_sha=escrow.source_sha, release_sha256=escrow.release_sha256)
    if args.operation == "import-images":
        manifest = load_artifact(local, args.backup_id, compatible=False, deadline=deadline.expires)
        path = args.local_store / ".offhost-escrow" / args.backup_id
        point = contract(private_file(path / "POINT.json", 1024**2), Completion)
        point.require(config, args.backup_id)
        escrow, release, _ = verify(path, manifest, config.max_bytes, deadline)
        if (
            hashlib.sha256(
                (local.store / args.backup_id / "manifest.json").read_bytes()
            ).hexdigest()
            != point.backup_manifest_sha256
            or manifest.snapshot != point.snapshot
            or manifest.completed_at != point.backup_completed_at
            or source_identity(manifest) != point.source_id
            or manifest.image_id != point.backend_image_id
            or manifest.source_sha != point.source_sha
            or escrow.release_sha256 != point.release_sha256
        ):
            raise RemoteError(Code.IDENTITY)
        imported = import_images(path, escrow, deadline)
        if args.output_configuration is not None:
            materialize_configuration(path, release, args.output_configuration, deadline)
        if args.output_overlay is not None:
            write_private(args.output_overlay, image_overlay(escrow, release, imported))
        return dict(
            backup_id=point.backup_id,
            source_sha=point.source_sha,
            images_imported=len(escrow.images),
            imported_image_ids={name: image.runtime_id for name, image in imported.items()},
            recovery_overlay_written=args.output_overlay is not None,
            recovery_configuration_written=args.output_configuration is not None,
        )
    remote = S3(config, deadline, credentials=args.credentials)
    if args.operation in {"upload", "retrieve", "status"}:
        telemetry(local, config, started, "pending")
    if args.operation == "upload":
        point = upload(local, remote, args.backup_id, args.escrow, args.age, work)
    elif args.operation == "retrieve":
        point, _ = retrieve(
            local, remote, args.backup_id, args.age, args.identity, temporary_root=work
        )
    elif args.operation == "retain":
        return retain(remote, work)
    else:
        points, ignored = inventory(remote)
        if not points:
            telemetry(local, config, started, "unknown")
            return dict(
                remote_copy_status="unknown",
                remote_copy_verified=False,
                point_count=0,
                ignored_entries=ignored,
            )
        point = max(
            (value for value, _ in points), key=lambda value: value.snapshot.established_after
        )
        verify_remote(remote, point, work)
        telemetry(local, config, started, "success", point)
        return dict(
            remote_copy_status="success",
            remote_copy_verified=True,
            point_count=len(points),
            ignored_entries=ignored,
        )
    telemetry(local, config, started, "success", point)
    return dict(
        backup_id=point.backup_id,
        source_sha=point.source_sha,
        backend_image_id=point.backend_image_id,
        configuration_sha256=point.configuration_sha256,
        backup_manifest_sha256=point.backup_manifest_sha256,
        release_sha256=point.release_sha256,
        encrypted_sha256=point.encrypted_sha256,
        remote_copy_status="success",
        remote_copy_verified=True,
        artifact_integrity_verified=True,
    )


def kill_owned(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def supervise(argv, args, config, started: datetime) -> Report:
    root = args.escrow.parent if args.operation == "prepare" else args.local_store
    jobs = directory(directory(root) / ".offhost-jobs")
    process = None
    with tempfile.TemporaryDirectory(prefix="job-", dir=jobs) as temporary:
        work = Path(temporary)
        if work.resolve().parent != jobs.resolve():
            raise RemoteError(Code.CONFIGURATION)
        env = dict(
            os.environ, PRL_OFFHOST_WORKSPACE=str(work), PRL_OFFHOST_STARTED_AT=started.isoformat()
        )
        try:
            with (work / "report.json").open("w+b") as output:
                if os.name != "nt":
                    os.fchmod(output.fileno(), 0o600)
                process = subprocess.Popen(
                    [sys.executable, "-m", "tools.backup.offhost", "--worker", *argv],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
                        if os.name == "nt"
                        else 0
                    ),
                )
                process.wait(timeout=config.max_seconds)
                if output.tell() > 16384:
                    raise RemoteError(Code.INTEGRITY)
                output.seek(0)
                report = contract(output.read(16385), Report)
                if report.operation != args.operation or report.success != (
                    process.returncode == 0
                ):
                    raise RemoteError(Code.INTEGRITY)
                return report
        except subprocess.TimeoutExpired:
            raise RemoteError(Code.TIMEOUT) from None
        finally:
            if process is not None:
                kill_owned(process)


def main(argv=None) -> int:
    args, config, started = None, None, time.monotonic()
    begun = datetime.now(UTC)
    argv = list(sys.argv[1:] if argv is None else argv)
    for name in ("botocore", "boto3", "urllib3", "asyncmy", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    try:
        args = arguments(argv)
        config = load_config(args.config)
        if args.worker:
            work = Path(os.environ["PRL_OFFHOST_WORKSPACE"])
            begun = datetime.fromisoformat(os.environ["PRL_OFFHOST_STARTED_AT"])
            result = perform(args, config, directory(work), begun)
            report = Report(
                operation=args.operation,
                success=True,
                duration_seconds=time.monotonic() - started,
                **result,
            )
        else:
            report = supervise(argv, args, config, begun)
        print(report.model_dump_json(exclude_none=True), flush=True)
        return 0 if report.success else 1
    except Exception as error:
        code = (
            error.code
            if isinstance(error, RemoteError)
            else Code.INTEGRITY if isinstance(error, BackupError) else Code.CONFIGURATION
        )
        if (
            args is not None
            and config is not None
            and args.operation in {"upload", "retrieve", "status"}
        ):
            telemetry(local_config(args, config), config, begun, "failure")
        report = Report(
            operation=args.operation if args is not None else "configuration",
            success=False,
            reason=code,
            duration_seconds=time.monotonic() - started,
        )
        print(report.model_dump_json(exclude_none=True), flush=True)
        return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
