"""Explicit operations: python -m app.backup --help (no DB access for help)."""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Sequence
from typing import NoReturn

from app.backup.backup import backup, backup_locked
from app.backup.config import BackupError, Reason, load_config
from app.backup.locking import operation_lock
from app.backup.manifest import Manifest
from app.backup.restore import restore, verify_restored
from app.backup.retention import retain, retain_locked, status
from app.backup.storage import load_artifact
from app.core.publishing import CLEANUP_TIMEOUT_SECONDS, Publisher

COMMANDS = ("backup", "restore", "verify", "inspect", "status", "retain", "scheduled")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise BackupError(Reason.CONFIGURATION)


def summary(manifest: Manifest, config) -> dict:
    return dict(
        backup_id=manifest.backup_id,
        completed_at=manifest.completed_at,
        schema_revision=manifest.source.revision,
        migration_sha256=manifest.source.migration_sha256,
        source_sha=manifest.source_sha,
        image_id=manifest.image_id,
        snapshot=manifest.snapshot.model_dump(),
        table_count=len(manifest.files),
        total_rows=sum(file.rows for file in manifest.files.values()),
        total_bytes=sum(file.bytes for file in manifest.files.values()),
        manifest_sha256=hashlib.sha256(
            (config.store / manifest.backup_id / "manifest.json").read_bytes()
        ).hexdigest(),
        artifact_integrity_verified=True,
        application_rehearsal_verified=False,
    )


def publish(reports: Publisher, operation: str, success: bool, started: float, **fields) -> None:
    reports.submit(
        json.dumps(
            dict(
                operation=operation,
                success=success,
                duration_seconds=time.monotonic() - started,
                **fields,
            ),
            sort_keys=True,
            allow_nan=False,
        )
    )


async def execute(argv: Sequence[str] | None) -> int:
    reports = Publisher()
    operation, started = "configuration", time.monotonic()
    try:
        parser = Parser(
            description="Consistent MySQL backup and verified separate-database recovery."
        )
        parser.add_argument("operation", choices=COMMANDS)
        parser.add_argument("--backup-id")
        parser.add_argument("--target-database")
        args = parser.parse_args(argv)
        operation = args.operation
        needs_id = operation in {"restore", "verify", "inspect"}
        needs_target = operation in {"restore", "verify"}
        if bool(args.backup_id) != needs_id or bool(args.target_database) != needs_target:
            raise BackupError(Reason.CONFIGURATION)
        config = load_config()
        if operation == "backup":
            result = summary(await backup(config), config)
        elif operation in {"restore", "verify"}:
            function = restore if operation == "restore" else verify_restored
            verified = await function(config, args.backup_id, args.target_database)
            result = dict(
                backup_id=verified["backup_id"],
                target_database=verified["target_database"],
                row_hashes_verified=verified["row_hashes_verified"],
                schema_verified=verified["schema_verified"],
                foreign_keys_checked=verified["foreign_keys_checked"],
                generated_columns_checked=verified["generated_columns_checked"],
                utc_verified=verified["utc_verified"],
                table_counts=verified["table_counts"],
                schema_sha256=verified["schema_sha256"],
                application_rehearsal_verified=False,
            )
        elif operation == "inspect":
            with operation_lock(config):
                result = summary(load_artifact(config, args.backup_id, compatible=False), config)
        elif operation == "status":
            result = status(config)
        elif operation == "retain":
            retention = retain(config)
            result = dict(
                removed_count=len(retention["removed"]),
                preserved_count=len(retention["preserved"]),
                ignored_entries=retention["ignored"],
            )
        else:
            with operation_lock(config):
                manifest = await backup_locked(config)
                publish(reports, "backup", True, started, **summary(manifest, config))
                retention = retain_locked(config)
                result = dict(
                    backup_id=manifest.backup_id,
                    removed_count=len(retention["removed"]),
                    preserved_count=len(retention["preserved"]),
                )
        publish(reports, operation, True, started, **result)
        return 0
    except BackupError as error:
        publish(reports, operation, False, started, reason=error.reason.value)
        return 2 if error.reason == Reason.CONFIGURATION else 1
    except TimeoutError:
        publish(reports, operation, False, started, reason=Reason.TIMEOUT.value)
        return 1
    except Exception:
        publish(reports, operation, False, started, reason=Reason.DATABASE.value)
        return 1
    finally:
        await reports.close(asyncio.get_running_loop().time() + CLEANUP_TIMEOUT_SECONDS)


def main(argv: Sequence[str] | None = None) -> int:
    for name in ("sqlalchemy", "asyncmy", "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    try:
        return asyncio.run(execute(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
