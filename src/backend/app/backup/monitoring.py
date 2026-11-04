"""Bounded, best-effort state for backup monitoring."""

import errno
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.backup.config import BackupConfig, BackupError, Reason, database_name
from app.backup.manifest import StrictModel, timestamp
from app.backup.retention import inventory
from app.backup.storage import sync_directory

STATE_FILENAME = ".prl-monitoring-state.json"
STATE_LOCK_FILENAME = ".prl-monitoring-state.lock"
MAX_STATE_BYTES = 65_536
MAX_INITIAL_SCAN_BYTES = 4 * 1024**3


class RemoteCopyState(StrictModel):
    configured: bool
    status: Literal["not_configured", "unknown", "pending", "success", "failure"]
    last_verified_at: datetime | None


class BackupMonitorState(StrictModel):
    schema_version: Literal[1]
    updated_at: datetime
    monitor_initialized_at: datetime | None = None
    last_verified_backup_at: datetime | None
    artifact_integrity_verified: bool | None
    application_rehearsal_verified: bool
    last_job_operation: Literal["backup", "scheduled"] | None
    last_job_started_at: datetime | None
    last_job_finished_at: datetime | None
    last_job_result: Literal["success", "failure"] | None
    last_job_reason: Reason | None
    last_job_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    last_scheduled_job_started_at: datetime | None = None
    last_scheduled_job_finished_at: datetime | None = None
    last_scheduled_job_result: Literal["success", "failure"] | None = None
    last_scheduled_job_duration_seconds: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    scheduled_job_failure_pending: bool = False
    minimum_free_bytes: int | None = Field(default=None, ge=0)
    remote_copy: RemoteCopyState | None = None

    @model_validator(mode="after")
    def valid_times(self) -> Self:
        values = [
            self.updated_at,
            self.monitor_initialized_at,
            self.last_verified_backup_at,
            self.last_job_started_at,
            self.last_job_finished_at,
            self.last_scheduled_job_started_at,
            self.last_scheduled_job_finished_at,
            self.remote_copy.last_verified_at if self.remote_copy else None,
        ]
        for value in values:
            if value is not None and (
                value.utcoffset() != timedelta(0) or value > datetime.now(UTC)
            ):
                raise ValueError("timestamps must be current UTC instants")
        if (
            self.last_job_started_at is not None
            and self.last_job_finished_at is not None
            and self.last_job_finished_at < self.last_job_started_at
        ):
            raise ValueError("job timestamps are reversed")
        return self


def _utc_datetime(value: str) -> datetime:
    return timestamp(value)


def read_state(path: Path, *, max_bytes: int = MAX_STATE_BYTES) -> BackupMonitorState | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            return None
        if os.name == "posix" and info.st_mode & 0o077:
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
                return None
            if os.name == "posix" and opened.st_mode & 0o077:
                return None
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        return BackupMonitorState.model_validate_json(raw)
    except Exception:
        return None


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate state field")
        result[key] = value
    return result


def _state_lock(root: Path) -> int | None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            root / STATE_LOCK_FILENAME,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
            os.close(descriptor)
            return None
        if info.st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        return None


def _unlock(descriptor: int) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _state_path_valid(path: Path) -> bool:
    try:
        root = path.parent
        return bool(
            path.name == STATE_FILENAME
            and root.is_absolute()
            and not root.is_symlink()
            and root.is_dir()
            and not (
                os.name == "posix"
                and (root.stat().st_mode & 0o077 or root.stat().st_uid != os.geteuid())
            )
        )
    except OSError:
        return False


def write_state(path: Path, state: BackupMonitorState) -> None:
    if not _state_path_valid(path):
        return
    descriptor = _state_lock(path.parent)
    if descriptor is None:
        return
    try:
        _write_state_locked(path, state)
    finally:
        try:
            _unlock(descriptor)
        except OSError:
            pass


def _write_state_locked(path: Path, state: BackupMonitorState) -> bool:
    temporary = None
    try:
        root = path.parent
        if not _state_path_valid(path) or (
            path.exists() and (path.is_symlink() or not path.is_file())
        ):
            return False
        raw = state.model_dump_json().encode()
        if len(raw) > MAX_STATE_BYTES:
            return False
        handle = tempfile.NamedTemporaryFile(dir=root, prefix=".prl-monitoring-", delete=False)
        temporary = Path(handle.name)
        with handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / STATE_FILENAME)
        temporary = None
        sync_directory(root)
        return True
    except Exception:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def initialize_state(path: Path, state: BackupMonitorState) -> BackupMonitorState | None:
    """Initialize once, preserving any valid state written while scanning."""
    if not _state_path_valid(path):
        return None
    descriptor = _state_lock(path.parent)
    if descriptor is None:
        return None
    try:
        if path.exists() or path.is_symlink():
            return read_state(path)
        return state if _write_state_locked(path, state) else None
    finally:
        try:
            _unlock(descriptor)
        except OSError:
            pass


def _update_state(
    path: Path,
    fallback: BackupMonitorState,
    transform: Callable[[BackupMonitorState], BackupMonitorState],
) -> None:
    """Read, mutate, and persist under one lock to avoid stale snapshot writes."""
    if not _state_path_valid(path):
        return
    descriptor = _state_lock(path.parent)
    if descriptor is None:
        return
    try:
        if path.exists() or path.is_symlink():
            state = read_state(path)
            if state is None:
                return
        else:
            state = fallback
        updated = transform(state)
        _write_state_locked(
            path,
            BackupMonitorState.model_validate(updated.model_dump(mode="python")),
        )
    finally:
        try:
            _unlock(descriptor)
        except OSError:
            pass


def collect_initial_state(config: BackupConfig, *, now: datetime) -> BackupMonitorState:
    if now.utcoffset() != timedelta(0) or now > datetime.now(UTC):
        raise BackupError(Reason.CONFIGURATION)
    deadline = time.monotonic() + min(config.max_seconds, 30)
    manifests, _ = inventory(config, deadline, max_total_bytes=MAX_INITIAL_SCAN_BYTES)
    name = database_name(config.database_url)
    values = [manifest for manifest in manifests if manifest.source.database == name]
    latest = max(
        values, key=lambda manifest: (manifest.completed_at, manifest.backup_id), default=None
    )
    completed = None
    verified = None
    if latest is not None:
        try:
            completed = _utc_datetime(latest.completed_at)
            if completed > now:
                completed = None
            else:
                verified = True
        except ValueError:
            pass
    return BackupMonitorState(
        schema_version=1,
        updated_at=now,
        monitor_initialized_at=now,
        last_verified_backup_at=completed,
        artifact_integrity_verified=verified,
        application_rehearsal_verified=False,
        last_job_operation=None,
        last_job_started_at=None,
        last_job_finished_at=None,
        last_job_result=None,
        last_job_reason=None,
        last_job_duration_seconds=None,
        minimum_free_bytes=config.min_free_bytes,
        remote_copy=None,
    )


def _current_or_initial(config: BackupConfig, now: datetime) -> BackupMonitorState:
    state = read_state(config.store / STATE_FILENAME)
    if state is not None:
        return state
    try:
        return collect_initial_state(config, now=now)
    except Exception:
        return BackupMonitorState(
            schema_version=1,
            updated_at=now,
            monitor_initialized_at=now,
            last_verified_backup_at=None,
            artifact_integrity_verified=None,
            application_rehearsal_verified=False,
            last_job_operation=None,
            last_job_started_at=None,
            last_job_finished_at=None,
            last_job_result=None,
            last_job_reason=None,
            last_job_duration_seconds=None,
            scheduled_job_failure_pending=False,
            minimum_free_bytes=None,
            remote_copy=None,
        )


def record_job_start(
    config: BackupConfig,
    *,
    operation: Literal["backup", "scheduled"],
    started_at: datetime,
) -> None:
    try:
        state = _current_or_initial(config, started_at)

        def update(current):
            updates = {}
            if current.last_job_started_at is None or started_at > current.last_job_started_at:
                updates.update(
                    last_job_operation=operation,
                    last_job_started_at=started_at,
                    last_job_finished_at=None,
                    last_job_result=None,
                    last_job_reason=None,
                    last_job_duration_seconds=None,
                )
            if operation == "scheduled" and (
                current.last_scheduled_job_started_at is None
                or started_at > current.last_scheduled_job_started_at
            ):
                updates["last_scheduled_job_started_at"] = started_at
            if not updates:
                return current
            updates["updated_at"] = max(current.updated_at, started_at)
            return current.model_copy(update=updates)

        _update_state(config.store / STATE_FILENAME, state, update)
    except Exception:
        pass


def record_job_finish(
    config: BackupConfig,
    *,
    operation: Literal["backup", "scheduled"],
    started_at: datetime | None = None,
    finished_at: datetime,
    success: bool,
    reason: Reason | None,
    duration_seconds: float,
    backup_completed_at: datetime | None,
    artifact_integrity_verified: bool | None,
) -> None:
    try:
        state = _current_or_initial(config, finished_at)

        def update(current):
            completed_backup = (
                backup_completed_at is not None and artifact_integrity_verified is True
            )
            latest_verified_backup_at = current.last_verified_backup_at
            if completed_backup and (
                latest_verified_backup_at is None or backup_completed_at > latest_verified_backup_at
            ):
                latest_verified_backup_at = backup_completed_at
            stale_job = (
                started_at is not None
                and current.last_job_started_at is not None
                and current.last_job_started_at > started_at
            )
            stale_scheduled_run = (
                operation == "scheduled"
                and started_at is not None
                and current.last_scheduled_job_started_at is not None
                and current.last_scheduled_job_started_at > started_at
            )
            if stale_scheduled_run:
                return current.model_copy(
                    update=dict(
                        updated_at=max(current.updated_at, finished_at),
                        last_verified_backup_at=latest_verified_backup_at,
                        artifact_integrity_verified=(
                            True if completed_backup else current.artifact_integrity_verified
                        ),
                    )
                )
            if stale_job:
                updates = dict(
                    updated_at=max(current.updated_at, finished_at),
                    last_verified_backup_at=latest_verified_backup_at,
                    artifact_integrity_verified=(
                        True if completed_backup else current.artifact_integrity_verified
                    ),
                )
                if operation == "scheduled":
                    updates.update(
                        last_scheduled_job_finished_at=finished_at,
                        last_scheduled_job_result="success" if success else "failure",
                        last_scheduled_job_duration_seconds=max(0.0, duration_seconds),
                        scheduled_job_failure_pending=not success,
                    )
                return current.model_copy(update=updates)
            job_started_at = current.last_job_started_at
            if (
                current.last_job_operation != operation
                or job_started_at is None
                or current.last_job_finished_at is not None
            ):
                job_started_at = (
                    started_at
                    or job_started_at
                    or (finished_at - timedelta(seconds=max(0.0, duration_seconds)))
                )
            scheduled_started_at = (
                job_started_at
                if operation == "scheduled"
                else current.last_scheduled_job_started_at
            )
            return current.model_copy(
                update=dict(
                    updated_at=finished_at,
                    last_verified_backup_at=latest_verified_backup_at,
                    artifact_integrity_verified=(
                        True if completed_backup else current.artifact_integrity_verified
                    ),
                    last_job_operation=operation,
                    last_job_started_at=job_started_at,
                    last_job_finished_at=finished_at,
                    last_job_result="success" if success else "failure",
                    last_job_reason=None if success else (reason or Reason.DATABASE),
                    last_job_duration_seconds=max(0.0, duration_seconds),
                    last_scheduled_job_started_at=(
                        scheduled_started_at
                        if operation == "scheduled"
                        else current.last_scheduled_job_started_at
                    ),
                    last_scheduled_job_finished_at=(
                        finished_at
                        if operation == "scheduled"
                        else current.last_scheduled_job_finished_at
                    ),
                    last_scheduled_job_result=(
                        ("success" if success else "failure")
                        if operation == "scheduled"
                        else current.last_scheduled_job_result
                    ),
                    last_scheduled_job_duration_seconds=(
                        max(0.0, duration_seconds)
                        if operation == "scheduled"
                        else current.last_scheduled_job_duration_seconds
                    ),
                    scheduled_job_failure_pending=(
                        not success
                        if operation == "scheduled"
                        else current.scheduled_job_failure_pending
                    ),
                )
            )

        _update_state(config.store / STATE_FILENAME, state, update)
    except Exception:
        pass
