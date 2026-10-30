"""Allowlisted public evidence; full service smoke defines recovery completion."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.backup.manifest import Count, Digest, SnapshotPoint, StrictModel, timestamp
from app.backup.schema import migration_contract
from app.models import Base

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Image = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
COMMANDS = [
    "compose build api frontend (source SHA labeled)",
    "source: migrate; apply-grants; seed (fictional only)",
    "app.backup backup",
    "source: later identifiable booking; record incident",
    "create separate empty recovery database",
    "app.backup restore --backup-id <id> --target-database <isolated>",
    "app.backup verify --backup-id <id> --target-database <isolated>",
    "target: apply reviewed runtime grants; start api worker frontend gateway",
    "target: login/history/detail/availability/book/replay/cancel/replay/logout",
    "compare source fingerprints",
]


class Images(StrictModel):
    api: Image
    worker: Image
    frontend: Image
    gateway: Image
    mysql: Image


class Backup(StrictModel):
    operation: Literal["backup"]
    success: Literal[True]
    duration_seconds: Seconds
    backup_id: str = Field(pattern=r"^prl-backup-\d{8}T\d{6}Z-[0-9a-f]{32}$")
    completed_at: str
    schema_revision: str
    migration_sha256: Digest
    source_sha: Sha
    image_id: Image
    snapshot: SnapshotPoint
    table_count: Count
    total_rows: Count
    total_bytes: Count
    manifest_sha256: Digest
    artifact_integrity_verified: Literal[True]
    application_rehearsal_verified: Literal[False]


class Validation(StrictModel):
    row_hashes_verified: Literal[True]
    schema_verified: Literal[True]
    foreign_keys_checked: Annotated[int, Field(strict=True, gt=0)]
    generated_columns_checked: Annotated[int, Field(strict=True, gt=0)]
    utc_verified: Literal[True]
    table_counts: dict[str, Count]
    schema_sha256: Digest


class Smoke(StrictModel):
    frontend: Literal[True]
    login: Literal[True]
    history: Literal[True]
    detail: Literal[True]
    availability: Literal[True]
    book: Literal[True]
    book_replay: Literal[True]
    cancel: Literal[True]
    cancel_replay: Literal[True]
    logout: Literal[True]
    side_effects_exact: Literal[True]
    later_change_excluded: Literal[True]


class Timing(StrictModel):
    incident_at: str
    recovery_started_at: str
    smoke_passed_at: str
    rpo_observed_min_seconds: Seconds
    rpo_observed_max_seconds: Seconds
    rto_service_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Evidence(StrictModel):
    evidence_version: Literal[1]
    accepted: Literal[True]
    source_sha: Sha
    pr_head_sha: Sha | None
    source_images: Images
    recovered_images: Images
    backup: Backup
    validation: Validation
    smoke: Smoke
    source_unchanged: Literal[True]
    target_migrated: Literal[False]
    target_seeded: Literal[False]
    timing: Timing
    commands: list[str]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        expected_tables = set(Base.metadata.tables) | {"alembic_version"}
        revision, digest = migration_contract()
        if (
            self.source_images != self.recovered_images
            or self.source_images.api != self.source_images.worker
            or self.source_images.api != self.backup.image_id
            or self.source_sha != self.backup.source_sha
            or self.commands != COMMANDS
            or self.backup.schema_revision != revision
            or self.backup.migration_sha256 != digest
            or set(self.validation.table_counts) != expected_tables
            or self.backup.table_count != len(expected_tables)
            or sum(self.validation.table_counts.values()) != self.backup.total_rows
            or any(
                self.validation.table_counts[table] < 1
                for table in ("appointments", "change_proposals", "audit_events", "notifications")
            )
        ):
            raise ValueError("Release, schema or verification evidence is inconsistent")
        timing, snapshot = self.timing, self.backup.snapshot
        incident = timestamp(timing.incident_at)
        # Incident and read-view bounds use the source DB's UTC clock. RTO uses
        # monotonic elapsed time; wall-clock stamps are evidence, not its clock.
        timestamp(self.backup.completed_at)
        started, passed = timestamp(timing.recovery_started_at), timestamp(timing.smoke_passed_at)
        if passed < started:
            raise ValueError("Service smoke must follow recovery start")
        low = (incident - timestamp(snapshot.established_after)).total_seconds()
        high = (incident - timestamp(snapshot.established_before)).total_seconds()
        if (
            low < 0
            or high < low
            or abs(timing.rpo_observed_min_seconds - low) > 0.000001
            or abs(timing.rpo_observed_max_seconds - high) > 0.000001
        ):
            raise ValueError("Observed data-loss interval does not match the snapshot")
        return self


def write_report(report: dict, path: Path) -> None:
    validated = Evidence.model_validate(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated.model_dump(), sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
