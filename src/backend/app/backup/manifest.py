"""Strict private artifact metadata; checksums provide integrity, not authenticity."""

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.backup.config import SYSTEM_DATABASES, BackupError, Reason, identifier
from app.backup.schema import migration_contract
from app.models import Base

BACKUP_ID = re.compile(r"prl-backup-\d{8}T\d{6}Z-[0-9a-f]{32}\Z")
MAX_MANIFEST_BYTES = 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TableDefinition(StrictModel):
    name: str
    ddl: str = Field(min_length=1, max_length=65536)
    columns: tuple[str, ...]
    writable: tuple[str, ...]
    primary_key: tuple[str, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        identifier(self.name)
        for column in self.columns:
            identifier(column)
        if not self.ddl.startswith(f"CREATE TABLE `{self.name}` (\n"):
            raise ValueError()
        # Only SHOW CREATE TABLE definitions are executable. Never accept scripts,
        # version comments, cross-database references or CREATE ... SELECT/file IO.
        masked = re.sub(r"'(?:\\.|''|[^'\\])*'", "", self.ddl)
        if re.search(
            r";|--|/\*|#|`\s*\.\s*`|\b(?:SELECT|OUTFILE|LOAD_FILE|DIRECTORY|TABLESPACE|CONNECTION|UNION)\b",
            masked,
            re.I,
        ):
            raise ValueError()
        if re.findall(r"\bENGINE\s*=\s*(\w+)", masked) != ["InnoDB"]:
            raise ValueError()
        if re.search(r"\bREFERENCES\s+(?!`[A-Za-z][A-Za-z0-9_]*`\s*\()", masked):
            raise ValueError()
        declared = tuple(re.findall(r"(?m)^  `([A-Za-z][A-Za-z0-9_]*)` ", self.ddl))
        keys = re.search(r"PRIMARY KEY\s*\(([^)]+)\)", self.ddl)
        if (
            not self.columns
            or len(set(self.columns)) != len(self.columns)
            or declared != self.columns
            or not self.writable
            or not set(self.writable) <= set(self.columns)
            or not self.primary_key
            or not set(self.primary_key) <= set(self.writable)
            or keys is None
            or tuple(re.findall(r"`([^`]+)`", keys[1])) != self.primary_key
            or not re.search(r"\)\s+ENGINE=InnoDB\b", self.ddl)
        ):
            raise ValueError()
        generated = tuple(
            column
            for column, declaration in re.findall(
                r"(?m)^  `([A-Za-z][A-Za-z0-9_]*)` (.*)$", self.ddl
            )
            if "GENERATED ALWAYS AS" in declaration
        )
        if tuple(column for column in self.columns if column not in generated) != self.writable:
            raise ValueError()
        return self


class SourceDescription(StrictModel):
    revision: str = Field(min_length=1, max_length=64)
    migration_sha256: Digest
    server_uuid: str
    server_version: str = Field(pattern=r"^8\.4\.[0-9]+[a-zA-Z0-9.+-]*$")
    charset: Literal["utf8mb4"]
    collation: str = Field(pattern=r"^utf8mb4_[a-z0-9_]+$")
    database: str
    tables: tuple[TableDefinition, ...]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if str(UUID(self.server_uuid)) != self.server_uuid:
            raise ValueError()
        identifier(self.database)
        if self.database.lower() in SYSTEM_DATABASES:
            raise ValueError()
        names = tuple(table.name for table in self.tables)
        if not names or len(names) > 64 or names != tuple(sorted(set(names))):
            raise ValueError()
        for table in self.tables:
            if not set(re.findall(r"REFERENCES `([^`]+)`", table.ddl)) <= set(names):
                raise ValueError()
        return self


class TableVerification(StrictModel):
    rows: Count
    bytes: Count
    sha256: Digest


def timestamp(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        instant.tzinfo != UTC
        or instant.isoformat(timespec="microseconds").replace("+00:00", "Z") != value
    ):
        raise ValueError()
    return instant


class SnapshotPoint(StrictModel):
    established_before: str
    established_after: str

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if timestamp(self.established_before) > timestamp(self.established_after):
            raise ValueError()
        return self


class Manifest(StrictModel):
    format: Literal["prl-mysql-jsonl"]
    format_version: int = Field(strict=True, ge=1, le=1)
    state: Literal["complete"]
    backup_id: str
    started_at: str
    completed_at: str
    snapshot: SnapshotPoint
    source: SourceDescription
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None
    image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None
    files: dict[str, TableVerification]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not BACKUP_ID.fullmatch(self.backup_id):
            raise ValueError()
        if timestamp(self.started_at) > timestamp(self.completed_at):
            raise ValueError()
        if set(self.files) != {table.name for table in self.source.tables}:
            raise ValueError()
        if self.files["alembic_version"].rows != 1:
            raise ValueError()
        return self


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def parse_manifest(
    raw: bytes, *, compatible: bool = True, expected_sha: str | None = None
) -> Manifest:
    try:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError()
        # Pydantic's JSON parser accepts repeated keys; reject them first.
        json.loads(raw, object_pairs_hook=no_duplicate_keys)
        result = Manifest.model_validate_json(raw)
    except Exception:
        raise BackupError(Reason.INTEGRITY) from None
    if compatible and (
        (result.source.revision, result.source.migration_sha256) != migration_contract()
        or (expected_sha is not None and result.source_sha != expected_sha)
    ):
        raise BackupError(Reason.INCOMPATIBLE)
    if compatible:
        tables = {table.name: table for table in result.source.tables}
        if set(tables) != set(Base.metadata.tables) | {"alembic_version"}:
            raise BackupError(Reason.INCOMPATIBLE)
        for name, table in tables.items():
            expected = (
                {"version_num"}
                if name == "alembic_version"
                else set(Base.metadata.tables[name].columns.keys())
            )
            if set(table.columns) != expected:
                raise BackupError(Reason.INCOMPATIBLE)
    return result


def manifest_bytes(value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    parse_manifest(raw)
    return raw
