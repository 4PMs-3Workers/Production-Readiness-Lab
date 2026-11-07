"""Versioned public off-host proof, separate from historical local recovery evidence."""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from tools.backup.evidence import Images, RecoveryFacts, Seconds
from tools.backup.offhost_contract import Completion

from app.backup.manifest import Digest, StrictModel, timestamp

COMMANDS = [
    "buildx immutable fixture images; simulated publication/review metadata",
    "source: reviewed migrations/grants/fictional seed; online app.backup backup",
    "offhost prepare: escrow exact images/migrations/release/configuration",
    "offhost upload: age encryption; HTTPS S3; full remote readback; conditional completion",
    "source: later committed booking; incident timestamp; quiesce and fingerprint",
    "stop source services/database and fixture registry; quarantine local export",
    "offhost retrieve into empty private target store; verify manifest/checksums/source",
    "offhost import-images from verified archives; use downloaded configuration",
    "separate target: app.backup restore and read-only schema/row verification",
    "recovered gateway: login/history/detail/availability/book/replay/cancel/replay/logout",
    "verify cancellation through detail and independent read-only DB status",
    "resume only source database for read-only unchanged-data comparison",
]
ENGINE_COMMANDS = [
    *COMMANDS[:6],
    "provision a separate containerd daemon; confirm empty images, containers and volumes",
    *COMMANDS[6:],
]


class Isolation(StrictModel):
    source_services_stopped: Literal[True]
    source_backup_unmounted: Literal[True]
    source_export_quarantined: Literal[True]
    registry_unavailable: Literal[True]
    images_imported_from_offhost: Literal[True]
    configuration_retrieved_from_offhost: Literal[True]
    source_unchanged: Literal[True]


class EngineImages(StrictModel):
    source_store: Literal["classic"]
    target_store: Literal["containerd"]
    target_initially_empty: Literal[True]
    archive_configuration_ids: Images
    imported_target_ids: Images


class OffhostEvidence(RecoveryFacts):
    format: Literal["prl-offhost-recovery-lab"]
    evidence_version: Literal[2, 3]
    transport: Literal["s3-age"]
    encryption: Literal["age-v1-X25519"]
    measurement_scope: Literal["isolated-automated-lab"]
    fixture_review_metadata: Literal[True]
    recovery_point: Completion
    release_configuration_sha256: Digest
    isolation: Isolation
    persisted_cancellation_verified: Literal[True]
    service_rto_clock: Literal["monotonic-recovery-start-through-smoke"]
    incident_to_smoke_wall_seconds: Seconds
    commands: list[str]
    engine_images: EngineImages | None = None

    @model_validator(mode="before")
    @classmethod
    def version(cls, data):
        if isinstance(data, dict) and type(data.get("evidence_version")) is not int:
            raise ValueError()
        return data

    @model_validator(mode="after")
    def binding(self) -> Self:
        point = self.recovery_point
        if self.evidence_version == 3:
            engines = self.engine_images
            if (
                engines is None
                or engines.archive_configuration_ids != self.source_images
                or engines.imported_target_ids != self.recovered_images
                or self.source_images.api == self.recovered_images.api
                or self.commands != ENGINE_COMMANDS
            ):
                raise ValueError("Source and recovery engine image evidence does not match")
        elif (
            self.engine_images is not None
            or self.source_images != self.recovered_images
            or self.commands != COMMANDS
        ):
            raise ValueError("Historical recovery image evidence does not match")
        if (
            point.backup_id != self.backup.backup_id
            or point.source_sha != self.source_sha
            or point.backend_image_id != self.backup.image_id
            or point.backup_manifest_sha256 != self.backup.manifest_sha256
            or point.snapshot != self.backup.snapshot
            or point.backup_completed_at != self.backup.completed_at
            or timestamp(self.timing.incident_at) > timestamp(self.timing.recovery_started_at)
            or abs(
                self.incident_to_smoke_wall_seconds
                - (
                    timestamp(self.timing.smoke_passed_at) - timestamp(self.timing.incident_at)
                ).total_seconds()
            )
            > 0.000001
        ):
            raise ValueError("Remote point, service recovery and source identity disagree")
        return self


def evidence(
    service: dict, point: Completion, checks: dict, configuration_sha256: str
) -> OffhostEvidence:
    facts = {
        key: value for key, value in service.items() if key not in {"evidence_version", "commands"}
    }
    separate_engine = "engine_images" in facts
    return OffhostEvidence.model_validate(
        dict(
            facts,
            format="prl-offhost-recovery-lab",
            evidence_version=3 if separate_engine else 2,
            transport="s3-age",
            encryption="age-v1-X25519",
            measurement_scope="isolated-automated-lab",
            fixture_review_metadata=True,
            recovery_point=point,
            isolation=checks,
            release_configuration_sha256=configuration_sha256,
            persisted_cancellation_verified=True,
            service_rto_clock="monotonic-recovery-start-through-smoke",
            incident_to_smoke_wall_seconds=(
                timestamp(service["timing"]["smoke_passed_at"])
                - timestamp(service["timing"]["incident_at"])
            ).total_seconds(),
            commands=ENGINE_COMMANDS if separate_engine else COMMANDS,
        )
    )


def write_report(report: OffhostEvidence, path: Path) -> None:
    validated = OffhostEvidence.model_validate_json(report.model_dump_json())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
