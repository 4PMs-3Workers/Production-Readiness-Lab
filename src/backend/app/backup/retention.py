"""Conservative per-source retention: a last verified success is never deleted."""

import os
import shutil
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.backup.config import BackupConfig, BackupError, Reason, database_name
from app.backup.locking import operation_lock
from app.backup.manifest import BACKUP_ID, timestamp
from app.backup.storage import load_artifact, store_root, sync_directory

SCAN_LIMIT = 20000


def inventory(config: BackupConfig, deadline: float):
    root = store_root(config)
    manifests, ignored, count = [], 0, 0
    with os.scandir(root) as entries:
        for entry in entries:
            count += 1
            if count > SCAN_LIMIT or time.monotonic() >= deadline:
                raise BackupError(Reason.TIMEOUT)
            if not BACKUP_ID.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                ignored += 1
                continue
            try:
                manifests.append(
                    load_artifact(config, entry.name, compatible=False, deadline=deadline)
                )
            except BackupError as error:
                if error.reason == Reason.TIMEOUT:
                    raise
                ignored += 1
    return manifests, ignored


def retain_locked(config: BackupConfig, *, now: datetime | None = None) -> dict:
    now = datetime.now(UTC) if now is None else now
    root = store_root(config)
    deadline = time.monotonic() + config.max_seconds
    manifests, ignored = inventory(config, deadline)
    groups = defaultdict(list)
    for manifest in manifests:
        groups[(manifest.source.server_uuid, manifest.source.database)].append(manifest)
    preserved, removed = [], []
    cutoff = now - timedelta(days=config.max_age_days)
    # Finish scanning/validating before deleting anything. Each group's newest
    # complete success survives regardless of configured age or count.
    for values in groups.values():
        values.sort(key=lambda item: (timestamp(item.completed_at), item.backup_id), reverse=True)
        for index, manifest in enumerate(values):
            expired = index >= config.keep_count or timestamp(manifest.completed_at) < cutoff
            if index == 0 or not expired:
                preserved.append(manifest.backup_id)
                continue
            if time.monotonic() >= deadline:
                raise BackupError(Reason.TIMEOUT)
            path = root / manifest.backup_id
            if path.is_symlink() or path.resolve().parent != root:
                raise BackupError(Reason.STORAGE)
            # Recheck the exact managed artifact immediately before removing its
            # directory; never recurse into unrelated/partial/symlink paths.
            load_artifact(config, manifest.backup_id, compatible=False, deadline=deadline)
            shutil.rmtree(path)
            removed.append(manifest.backup_id)
    sync_directory(root)
    return dict(preserved=preserved, removed=removed, ignored=ignored)


def retain(config: BackupConfig, *, now: datetime | None = None) -> dict:
    with operation_lock(config):
        return retain_locked(config, now=now)


def status(config: BackupConfig) -> dict:
    with operation_lock(config):
        manifests, ignored = inventory(config, time.monotonic() + config.max_seconds)
        values = [
            value
            for value in manifests
            if value.source.database == database_name(config.database_url)
        ]
        latest = max(values, key=lambda value: (value.completed_at, value.backup_id), default=None)
        return dict(
            last_successful_backup=latest.backup_id if latest is not None else None,
            completed_at=latest.completed_at if latest is not None else None,
            backup_age_seconds=(
                max(0.0, (datetime.now(UTC) - timestamp(latest.completed_at)).total_seconds())
                if latest is not None
                else None
            ),
            complete_artifacts=len(values),
            ignored_entries=ignored,
            artifact_integrity_verified=latest is not None,
            application_rehearsal_verified=False,
        )
