"""Post-run consistency checks over the isolated fixture schema."""

from collections import Counter
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tools.load.runner import WorkloadResult

from app.models import AuditEvent, ChangeProposal, Notification

_BATCH_SIZE = 1_000


async def verify_post_run_invariants(
    database_url: str, workload: WorkloadResult, *, expected_release_sha: str
) -> dict[str, object]:
    """Return only aggregate outcomes; never include IDs, account data, or payloads."""
    engine = create_async_engine(
        database_url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        async with engine.connect() as connection:
            overlap_count = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM appointments a JOIN appointments b ON a.id < b.id
                  AND a.status IN ('UPCOMING', 'CHANGE_PENDING')
                  AND b.status IN ('UPCOMING', 'CHANGE_PENDING')
                  AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at
                  AND (a.staff_id = b.staff_id OR a.student_id = b.student_id)
                """,
            )
            quota_violations = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM (
                    SELECT student_id FROM appointments
                    WHERE status IN ('UPCOMING', 'CHANGE_PENDING')
                    GROUP BY student_id HAVING COUNT(*) > 3
                ) over_quota
                """,
            )
            proposal_mismatches = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM appointments a
                LEFT JOIN (
                    SELECT appointment_id, SUM(status = 'PENDING') AS pending_count
                    FROM change_proposals GROUP BY appointment_id
                ) p ON p.appointment_id = a.id
                WHERE (a.status = 'CHANGE_PENDING' AND COALESCE(p.pending_count, 0) <> 1)
                   OR (a.status <> 'CHANGE_PENDING' AND COALESCE(p.pending_count, 0) > 0)
                """,
            )
            version_mismatches = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM change_proposals p
                JOIN appointments a ON a.id = p.appointment_id
                WHERE p.created_version IS NOT NULL AND p.created_version > a.proposal_version
                """,
            )
            ownership = await connection.execute(
                text("SELECT source_sha, fixture_count FROM prl_load_ownership LIMIT 1")
            )
            ownership_row = ownership.one_or_none()
            booking_effects = await _effect_mismatches(
                connection,
                workload.created_booking_ids,
                audit_action="APPOINTMENT_CREATED",
                notification_action="APPOINTMENT_CREATED",
            )
            cancellation_effects = await _effect_mismatches(
                connection,
                workload.cancelled_appointment_ids,
                audit_action="APPOINTMENT_CANCELLED",
                notification_action="APPOINTMENT_CANCELLED",
            )
            proposal_effects = await _proposal_effect_mismatches(
                connection, workload.created_proposal_ids
            )
            duplicate_keys = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM (
                    SELECT student_id, creation_request_key FROM appointments
                    GROUP BY student_id, creation_request_key HAVING COUNT(*) > 1
                ) duplicates
                """,
            )
            duplicate_proposal_keys = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM (
                    SELECT appointment_id, creation_request_key FROM change_proposals
                    WHERE creation_request_key IS NOT NULL
                    GROUP BY appointment_id, creation_request_key HAVING COUNT(*) > 1
                ) duplicates
                """,
            )
    finally:
        await engine.dispose()

    replay_expected = len(workload.created_booking_ids) + len(workload.created_proposal_ids)
    checks = {
        "ownership_marker_matches_release": {
            "passed": ownership_row is not None and ownership_row[0] == expected_release_sha,
            "fixture_appointments": int(ownership_row[1]) if ownership_row is not None else None,
        },
        "staff_and_student_overlap_count": {
            "passed": overlap_count == 0,
            "violations": overlap_count,
        },
        "three_occupying_appointment_limit": {
            "passed": quota_violations == 0,
            "violations": quota_violations,
        },
        "proposal_appointment_state": {
            "passed": proposal_mismatches == 0 and version_mismatches == 0,
            "state_mismatches": proposal_mismatches,
            "version_mismatches": version_mismatches,
        },
        "booking_audit_and_notifications": booking_effects,
        "cancellation_audit_and_notifications": cancellation_effects,
        "proposal_audit_and_notifications": proposal_effects,
        "idempotency_replay": {
            "passed": workload.verified_replay_count == replay_expected,
            "verified_same_receipt_replays": workload.verified_replay_count,
            "expected_replays": replay_expected,
            "duplicate_booking_keys": duplicate_keys,
            "duplicate_proposal_keys": duplicate_proposal_keys,
        },
    }
    replay_check = checks["idempotency_replay"]
    assert isinstance(replay_check, dict)
    replay_check["passed"] = (
        replay_check["passed"] and duplicate_keys == 0 and duplicate_proposal_keys == 0
    )
    return {"passed": all(bool(check["passed"]) for check in checks.values()), "checks": checks}


async def _scalar(connection, statement: str) -> int:
    result = await connection.execute(text(statement))
    return int(result.scalar_one())


async def _effect_mismatches(
    connection,
    appointment_ids: list[UUID],
    *,
    audit_action: str,
    notification_action: str,
) -> dict[str, object]:
    if not appointment_ids:
        return {"passed": True, "verified_records": 0, "mismatches": 0}
    audit_counts: Counter[str] = Counter()
    notification_counts: Counter[UUID] = Counter()
    for batch in _chunks(appointment_ids):
        string_ids = [str(value) for value in batch]
        audit_rows = await connection.execute(
            select(AuditEvent.entity_id, func.count())
            .where(
                AuditEvent.entity_type == "APPOINTMENT",
                AuditEvent.action == audit_action,
                AuditEvent.entity_id.in_(string_ids),
            )
            .group_by(AuditEvent.entity_id)
        )
        audit_counts.update({entity_id: int(count) for entity_id, count in audit_rows})
        notification_rows = await connection.execute(
            select(Notification.appointment_id, func.count())
            .where(
                Notification.event_type == notification_action,
                Notification.appointment_id.in_(batch),
            )
            .group_by(Notification.appointment_id)
        )
        notification_counts.update(
            {appointment_id: int(count) for appointment_id, count in notification_rows}
        )
    mismatches = sum(
        audit_counts[str(appointment_id)] != 1 or notification_counts[appointment_id] != 2
        for appointment_id in appointment_ids
    )
    return {
        "passed": mismatches == 0,
        "verified_records": len(appointment_ids),
        "mismatches": mismatches,
    }


async def _proposal_effect_mismatches(connection, proposal_ids: list[UUID]) -> dict[str, object]:
    if not proposal_ids:
        return {"passed": True, "verified_records": 0, "mismatches": 0}
    audit_counts: Counter[UUID] = Counter()
    action_by_proposal: dict[UUID, str] = {}
    notification_counts: Counter[tuple[UUID, str]] = Counter()
    appointment_by_proposal: dict[UUID, UUID] = {}
    for batch in _chunks(proposal_ids):
        string_ids = [str(value) for value in batch]
        audit_rows = await connection.execute(
            select(AuditEvent.entity_id, AuditEvent.action, func.count())
            .where(
                AuditEvent.entity_type == "CHANGE_PROPOSAL",
                AuditEvent.action.in_({"CHANGE_PROPOSAL_CREATED", "CHANGE_PROPOSAL_REPLACED"}),
                AuditEvent.entity_id.in_(string_ids),
            )
            .group_by(AuditEvent.entity_id, AuditEvent.action)
        )
        for entity_id, action, count in audit_rows:
            proposal_id = UUID(entity_id)
            audit_counts[proposal_id] += int(count)
            action_by_proposal[proposal_id] = action
        proposal_rows = await connection.execute(
            select(ChangeProposal.id, ChangeProposal.appointment_id).where(
                ChangeProposal.id.in_(batch)
            )
        )
        appointment_by_proposal.update(
            {proposal_id: appointment_id for proposal_id, appointment_id in proposal_rows}
        )
    expected_notifications: Counter[tuple[UUID, str]] = Counter()
    mismatches = sum(audit_counts[proposal_id] != 1 for proposal_id in proposal_ids)
    for proposal_id in proposal_ids:
        appointment_id = appointment_by_proposal.get(proposal_id)
        action = action_by_proposal.get(proposal_id)
        if appointment_id is None or action is None:
            mismatches += 1
            continue
        expected_notifications[(appointment_id, action)] += 2
    for batch in _chunks(list(appointment_by_proposal.values())):
        notification_rows = await connection.execute(
            select(Notification.appointment_id, Notification.event_type, func.count())
            .where(
                Notification.appointment_id.in_(batch),
                Notification.event_type.in_(
                    {"CHANGE_PROPOSAL_CREATED", "CHANGE_PROPOSAL_REPLACED"}
                ),
            )
            .group_by(Notification.appointment_id, Notification.event_type)
        )
        notification_counts.update(
            {
                (appointment_id, event_type): int(count)
                for appointment_id, event_type, count in notification_rows
            }
        )
    for key in expected_notifications.keys() | notification_counts.keys():
        if notification_counts[key] != expected_notifications[key]:
            mismatches += 1
    return {
        "passed": mismatches == 0,
        "verified_records": len(proposal_ids),
        "mismatches": mismatches,
    }


def _chunks(values: list[UUID], size: int = _BATCH_SIZE) -> list[list[UUID]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
