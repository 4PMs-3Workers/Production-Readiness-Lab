"""Explicit isolated restore; only fully verified data receives a success result."""

import asyncio
import hashlib
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.importing import create_tables, insert_rows
from app.backup.locking import operation_lock
from app.backup.snapshot import consistent_snapshot
from app.backup.storage import load_artifact
from app.backup.target import empty_target
from app.backup.verification import verify_database


async def restore(config: BackupConfig, backup_id: str, target_database: str) -> dict:
    with operation_lock(config):
        return await restore_locked(config, backup_id, target_database)


async def restore_locked(config: BackupConfig, backup_id: str, target_database: str) -> dict:
    deadline = time.monotonic() + config.max_seconds
    try:
        async with asyncio.timeout(config.max_seconds):
            # Complete, bounded artifact preflight happens before target DDL/DML.
            manifest = load_artifact(config, backup_id, deadline=deadline)
            async with empty_target(config, manifest, target_database) as db:
                try:
                    await create_tables(db, manifest, config.query_seconds)
                    async with db.begin():
                        await insert_rows(
                            db, manifest, config.store / backup_id, config.query_seconds, deadline
                        )
                        await db.execute(text("SET SESSION FOREIGN_KEY_CHECKS=1"))
                        result = await verify_database(db, manifest, deadline, config.query_seconds)
                finally:
                    try:
                        async with asyncio.timeout(5):
                            await db.rollback()
                            await db.execute(text("SET SESSION FOREIGN_KEY_CHECKS=1"))
                            await db.commit()
                    except (SQLAlchemyError, TimeoutError):
                        await db.invalidate()
            return dict(
                backup_id=backup_id,
                target_database=target_database,
                source_sha=manifest.source_sha,
                image_id=manifest.image_id,
                schema_revision=manifest.source.revision,
                migration_sha256=manifest.source.migration_sha256,
                manifest_sha256=hashlib.sha256(
                    (config.store / backup_id / "manifest.json").read_bytes()
                ).hexdigest(),
                snapshot=manifest.snapshot.model_dump(),
                **result,
            )
    except TimeoutError:
        raise BackupError(Reason.TIMEOUT) from None
    except OSError:
        raise BackupError(Reason.INTEGRITY) from None


async def verify_restored(config: BackupConfig, backup_id: str, target_database: str) -> dict:
    with operation_lock(config):
        deadline = time.monotonic() + config.max_seconds
        async with asyncio.timeout(config.max_seconds):
            manifest = load_artifact(config, backup_id, deadline=deadline)
            async with empty_target(config, manifest, target_database, require_empty=False) as db:
                async with consistent_snapshot(db):
                    result = await verify_database(db, manifest, deadline, config.query_seconds)
            return dict(backup_id=backup_id, target_database=target_database, **result)
