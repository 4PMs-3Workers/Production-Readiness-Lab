"""Strict, payload-free contracts for the single HTTPS S3/age transport."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from app.backup.manifest import (
    BACKUP_ID,
    Digest,
    Manifest,
    SnapshotPoint,
    StrictModel,
    no_duplicate_keys,
    timestamp,
)

Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Image = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
MAX_CONTRACT = 1024 * 1024


class Code(StrEnum):
    CONFIGURATION = "REMOTE_CONFIGURATION_INVALID"
    KEY = "REMOTE_KEY_UNAVAILABLE"
    IDENTITY = "REMOTE_SOURCE_IDENTITY_MISMATCH"
    INTEGRITY = "REMOTE_ARTIFACT_INVALID"
    TRANSPORT = "REMOTE_TRANSPORT_FAILED"
    TIMEOUT = "REMOTE_DEADLINE_EXCEEDED"
    BUSY = "REMOTE_OPERATION_BUSY"
    CONFLICT = "REMOTE_IMMUTABLE_CONFLICT"
    IMAGE = "REMOTE_APPLICATION_ESCROW_INVALID"


class RemoteError(Exception):
    def __init__(self, code: Code):
        self.code = code
        super().__init__(code.value)


def canonical(value: StrictModel | dict) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, StrictModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


T = TypeVar("T", bound=StrictModel)


def contract(raw: bytes, model: type[T]) -> T:
    try:
        if len(raw) > MAX_CONTRACT:
            raise ValueError()
        json.loads(raw, object_pairs_hook=no_duplicate_keys)
        return model.model_validate_json(raw)
    except (ValueError, TypeError):
        raise RemoteError(Code.INTEGRITY) from None


def private_file(path: Path, maximum: int, *, secret: bool = True) -> bytes:
    try:
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError()
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError()
        if secret and os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise ValueError()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                info.st_dev,
                info.st_ino,
                info.st_size,
            ):
                raise ValueError()
            value = source.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise RemoteError(Code.KEY if secret else Code.CONFIGURATION) from None


class Credentials(StrictModel):
    access_key: str = Field(min_length=3, max_length=256, repr=False)
    secret_key: str = Field(min_length=8, max_length=1024, repr=False)
    session_token: str | None = Field(default=None, max_length=16384, repr=False)


class Versioned(StrictModel):
    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, data):
        if (
            isinstance(data, dict)
            and "format_version" in data
            and type(data["format_version"]) is not int
        ):
            raise ValueError()
        return data


class RemoteConfig(Versioned):
    format_version: Literal[1] = 1
    backend: Literal["s3-age"] = "s3-age"
    endpoint: str = Field(repr=False)
    region: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", repr=False)
    prefix: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$", repr=False)
    environment: Slug
    store: Slug
    source_id: Digest = Field(repr=False)
    recipient: str = Field(pattern=r"^age1[0-9a-z]{58}$", repr=False)
    ca_file: str | None = Field(default=None, repr=False)
    max_seconds: int = Field(default=900, ge=1, le=3600)
    request_seconds: int = Field(default=10, ge=1, le=30)
    attempts: int = Field(default=3, ge=1, le=5)
    max_bytes: int = Field(default=4 * 1024**3, ge=1024, le=4 * 1024**3)
    min_free_bytes: int = Field(default=64 * 1024**2, ge=0, le=2**40)
    keep_count: int = Field(default=7, ge=1, le=1000)
    max_age_days: int = Field(default=30, ge=1, le=36500)

    @model_validator(mode="after")
    def secure(self):
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or ".." in self.bucket
            or ".-" in self.bucket
            or "-." in self.bucket
            or (self.ca_file is not None and not Path(self.ca_file).is_absolute())
            or type(self.format_version) is not int
        ):
            raise ValueError()
        # Force port validation and normalize no hidden authority/path alternatives.
        _ = url.port
        return self

    @property
    def scope(self) -> str:
        return f"{self.prefix}/{self.environment}/{self.source_id}/"

    @property
    def identity(self) -> str:
        # Retention limits/deadlines can change without invalidating old points.
        data = self.model_dump(
            include={
                "backend",
                "endpoint",
                "region",
                "bucket",
                "prefix",
                "environment",
                "store",
                "source_id",
                "recipient",
            }
        )
        return hashlib.sha256(canonical(data)).hexdigest()


def load_config(path: Path) -> RemoteConfig:
    try:
        return contract(private_file(path, MAX_CONTRACT), RemoteConfig)
    except RemoteError:
        raise RemoteError(Code.CONFIGURATION) from None


def source_identity(manifest: Manifest) -> str:
    return hashlib.sha256(
        canonical(
            dict(
                server_uuid=manifest.source.server_uuid,
                database=manifest.source.database,
            )
        )
    ).hexdigest()


class Completion(Versioned):
    format: Literal["prl-offhost-point"] = "prl-offhost-point"
    format_version: Literal[1, 2] = 1
    state: Literal["complete"] = "complete"
    backup_id: str
    source_id: Digest
    configuration_sha256: Digest
    source_sha: Sha
    backend_image_id: Image
    backup_manifest_sha256: Digest
    release_sha256: Digest
    bundle_sha256: Digest
    encrypted_sha256: Digest
    encrypted_bytes: int = Field(gt=0, le=4 * 1024**3 + 16 * 1024**2)
    snapshot: SnapshotPoint
    backup_completed_at: str
    verified_at: str

    @model_validator(mode="after")
    def coherent(self):
        if not BACKUP_ID.fullmatch(self.backup_id) or type(self.format_version) is not int:
            raise ValueError()
        if not (
            timestamp(self.snapshot.established_after)
            <= timestamp(self.backup_completed_at)
            <= timestamp(self.verified_at)
            <= datetime.now(UTC)
        ):
            raise ValueError()
        return self

    def require(self, config: RemoteConfig, backup_id: str) -> None:
        if (
            self.backup_id != backup_id
            or self.source_id != config.source_id
            or self.configuration_sha256 != config.identity
            or self.encrypted_bytes > config.max_bytes + 16 * 1024**2
        ):
            raise RemoteError(Code.IDENTITY)


def point_key(config: RemoteConfig, backup_id: str) -> str:
    if not BACKUP_ID.fullmatch(backup_id):
        raise RemoteError(Code.IDENTITY)
    return f"{config.scope}points/{backup_id}/complete.json"


def payload_key(config: RemoteConfig, point: Completion) -> str:
    point.require(config, point.backup_id)
    return blob_key(config, point.backup_id, point.encrypted_sha256)


class Retirement(Versioned):
    format: Literal["prl-offhost-retirement"] = "prl-offhost-retirement"
    format_version: Literal[1] = 1
    state: Literal["retired"] = "retired"
    point: Completion
    retired_at: str

    @model_validator(mode="after")
    def ordered(self):
        if not timestamp(self.point.verified_at) <= timestamp(self.retired_at) <= datetime.now(UTC):
            raise ValueError()
        return self


def blob_key(config: RemoteConfig, backup_id: str, sha256: str) -> str:
    import re

    point_key(config, backup_id)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RemoteError(Code.IDENTITY)
    return f"{config.scope}points/{backup_id}/blobs/{sha256}.age"
