"""Explicit recovery configuration; credentials stay in the external environment."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from sqlalchemy.engine import make_url

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SYSTEM_DATABASES = {"mysql", "sys", "information_schema", "performance_schema"}


class Reason(StrEnum):
    CONFIGURATION = "CONFIGURATION_INVALID"
    INTEGRITY = "ARTIFACT_INVALID"
    INCOMPATIBLE = "SCHEMA_OR_RELEASE_INCOMPATIBLE"
    UNSUPPORTED = "UNSUPPORTED_SOURCE_SCHEMA"
    BUSY = "OPERATION_ALREADY_RUNNING"
    WRONG_TARGET = "UNSAFE_RESTORE_TARGET"
    NONEMPTY = "RESTORE_TARGET_NOT_EMPTY"
    STORAGE = "STORAGE_UNAVAILABLE_OR_CAPACITY_EXCEEDED"
    DATABASE = "DATABASE_OPERATION_FAILED"
    VALIDATION = "RESTORED_DATA_VALIDATION_FAILED"
    TIMEOUT = "OPERATION_DEADLINE_EXCEEDED"
    SMOKE = "RESTORED_APPLICATION_SMOKE_FAILED"


class BackupError(Exception):
    def __init__(self, reason: Reason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def database_name(url: str) -> str:
    try:
        parsed = make_url(url)
        name = parsed.database or ""
        if (
            parsed.drivername != "mysql+asyncmy"
            or not parsed.host
            or not parsed.username
            or not IDENTIFIER.fullmatch(name)
            or name.lower() in SYSTEM_DATABASES
        ):
            raise ValueError()
        return name
    except Exception:
        raise BackupError(Reason.CONFIGURATION) from None


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise BackupError(Reason.UNSUPPORTED)
    return f"`{value}`"


def _integer(values: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    try:
        result = int(values.get(key, str(default)))
        if not low <= result <= high:
            raise ValueError()
        return result
    except ValueError:
        raise BackupError(Reason.CONFIGURATION) from None


@dataclass(frozen=True)
class BackupConfig:
    database_url: str = field(repr=False)
    store: Path
    target_url: str | None = field(default=None, repr=False)
    source_sha: str | None = None
    image_id: str | None = None
    max_seconds: int = 600
    query_seconds: int = 30
    lock_seconds: int = 3
    max_bytes: int = 512 * 1024 * 1024
    min_free_bytes: int = 64 * 1024 * 1024
    keep_count: int = 7
    max_age_days: int = 30


def load_config(values: Mapping[str, str] | None = None) -> BackupConfig:
    env = os.environ if values is None else values
    source = env.get("DATABASE_URL", "")
    database_name(source)
    target = env.get("RESTORE_DATABASE_URL") or None
    if target is not None:
        database_name(target)
    sha, image = env.get("BACKUP_SOURCE_SHA") or None, env.get("BACKUP_IMAGE_ID") or None
    if (sha is not None and not SHA.fullmatch(sha)) or (
        image is not None and not IMAGE.fullmatch(image)
    ):
        raise BackupError(Reason.CONFIGURATION)
    store = env.get("BACKUP_STORE", "")
    if not store or not Path(store).is_absolute():
        raise BackupError(Reason.CONFIGURATION)
    return BackupConfig(
        source,
        Path(store),
        target,
        sha,
        image,
        max_seconds=_integer(env, "BACKUP_MAX_SECONDS", 600, 1, 3600),
        query_seconds=_integer(env, "BACKUP_QUERY_SECONDS", 30, 1, 120),
        lock_seconds=_integer(env, "BACKUP_LOCK_SECONDS", 3, 1, 30),
        max_bytes=_integer(env, "BACKUP_MAX_BYTES", 512 * 1024 * 1024, 1024, 2**40),
        min_free_bytes=_integer(env, "BACKUP_MIN_FREE_BYTES", 64 * 1024 * 1024, 0, 2**40),
        keep_count=_integer(env, "BACKUP_KEEP_COUNT", 7, 1, 10000),
        max_age_days=_integer(env, "BACKUP_MAX_AGE_DAYS", 30, 1, 36500),
    )
