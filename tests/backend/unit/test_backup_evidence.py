import json
from typing import Any

import pytest
from tools.backup.evidence import COMMANDS, Evidence, write_report

from app.backup.schema import migration_contract
from app.models import Base


def report() -> dict[str, Any]:
    revision, digest = migration_contract()
    tables = {table: 1 for table in sorted(set(Base.metadata.tables) | {"alembic_version"})}
    images = {
        name: "sha256:" + "a" * 64 for name in ("api", "worker", "frontend", "gateway", "mysql")
    }
    return dict(
        evidence_version=1,
        accepted=True,
        source_sha="b" * 40,
        pr_head_sha=None,
        source_images=images,
        recovered_images=dict(images),
        backup=dict(
            operation="backup",
            success=True,
            duration_seconds=0.8,
            backup_id="prl-backup-20261028T120000Z-" + "a" * 32,
            completed_at="2026-10-28T12:00:00.500000Z",
            schema_revision=revision,
            migration_sha256=digest,
            source_sha="b" * 40,
            image_id=images["api"],
            snapshot=dict(
                established_before="2026-10-28T12:00:00.000000Z",
                established_after="2026-10-28T12:00:00.100000Z",
            ),
            table_count=len(tables),
            total_rows=sum(tables.values()),
            total_bytes=1024,
            manifest_sha256="c" * 64,
            artifact_integrity_verified=True,
            application_rehearsal_verified=False,
        ),
        validation=dict(
            row_hashes_verified=True,
            schema_verified=True,
            foreign_keys_checked=20,
            generated_columns_checked=1,
            utc_verified=True,
            table_counts=tables,
            schema_sha256="d" * 64,
        ),
        smoke={
            name: True
            for name in (
                "frontend",
                "login",
                "history",
                "detail",
                "availability",
                "book",
                "book_replay",
                "cancel",
                "cancel_replay",
                "logout",
                "side_effects_exact",
                "later_change_excluded",
            )
        },
        source_unchanged=True,
        target_migrated=False,
        target_seeded=False,
        timing=dict(
            incident_at="2026-10-28T12:00:02.100000Z",
            recovery_started_at="2026-10-28T12:00:02.200000Z",
            smoke_passed_at="2026-10-28T12:00:12.200000Z",
            rpo_observed_min_seconds=2.0,
            rpo_observed_max_seconds=2.1,
            rto_service_seconds=10.0,
        ),
        commands=list(COMMANDS),
    )


def test_public_evidence_records_service_smoke_and_snapshot_based_interval(tmp_path):
    value = report()
    destination = tmp_path / "rehearsal.json"
    write_report(value, destination)
    assert json.loads(destination.read_text()) == value


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("smoke", "logout", False),
        ("timing", "rpo_observed_max_seconds", 0.0),
        ("timing", "rto_service_seconds", float("nan")),
        ("timing", "smoke_passed_at", "2026-10-28T12:00:01.000000Z"),
        ("recovered_images", "worker", "sha256:" + "e" * 64),
        ("backup", "source_sha", "e" * 40),
        ("validation", "credentials", "fictional-do-not-publish"),
        ("backup", "total_rows", 0),
    ],
)
def test_incomplete_inconsistent_or_non_allowlisted_evidence_is_rejected(section, key, value):
    evidence = report()
    evidence[section][key] = value
    with pytest.raises(ValueError):
        Evidence.model_validate(evidence)


def test_arbitrary_commands_or_payloads_cannot_enter_public_report():
    value = report()
    value["commands"] = ["arbitrary command containing a fictional account"]
    with pytest.raises(ValueError):
        Evidence.model_validate(value)
    value = report()
    value["validation"]["table_counts"]["appointments"] = 0
    value["backup"]["total_rows"] -= 1
    with pytest.raises(ValueError):
        Evidence.model_validate(value)
