"""Bounded orchestration from an online source snapshot to a complete artifact."""

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.locking import operation_lock
from app.backup.manifest import Manifest
from app.backup.schema import open_source
from app.backup.snapshot import consistent_snapshot, dump_tables, utc
from app.backup.storage import staging


async def backup(config: BackupConfig) -> Manifest:
    with operation_lock(config):
        return await backup_locked(config)


async def backup_locked(config: BackupConfig) -> Manifest:
    started = datetime.now(UTC)
    identity = "prl-backup-" + started.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex
    deadline = time.monotonic() + config.max_seconds
    try:
        async with asyncio.timeout(config.max_seconds):
            with staging(config, identity) as owned:
                async with open_source(config) as (db, schema):
                    async with consistent_snapshot(db) as point:
                        files = await dump_tables(
                            db,
                            schema,
                            owned.path,
                            config.max_bytes,
                            config.min_free_bytes,
                            config.query_seconds,
                        )
                value = dict(
                    format="prl-mysql-jsonl",
                    format_version=1,
                    state="complete",
                    backup_id=identity,
                    started_at=utc(started),
                    completed_at=utc(datetime.now(UTC)),
                    snapshot=point,
                    source=asdict(schema),
                    source_sha=config.source_sha,
                    image_id=config.image_id,
                    files=files,
                )
                return owned.publish(value, deadline=deadline)
    except TimeoutError:
        raise BackupError(Reason.TIMEOUT) from None
    except OSError:
        raise BackupError(Reason.STORAGE) from None
