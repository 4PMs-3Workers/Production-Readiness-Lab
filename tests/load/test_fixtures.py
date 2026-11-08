from datetime import UTC, datetime

import pytest
from tools.load.config import LoadSafetyError
from tools.load.fixtures import make_fixture_rows, validate_cleanup_ownership


def test_fixture_is_reproducible_and_has_required_relationships():
    generated_at = datetime(2026, 11, 8, tzinfo=UTC)
    first = make_fixture_rows(seed=8128, generated_at=generated_at, password_hash="fixture-hash")
    second = make_fixture_rows(seed=8128, generated_at=generated_at, password_hash="fixture-hash")

    assert len(first.staff) == 10
    assert len(first.students) == 40
    assert len(first.appointments) == 1_000
    assert [row.id for row in first.appointments] == [row.id for row in second.appointments]
    assert all(row.staff_id in {staff.id for staff in first.staff} for row in first.appointments)
    assert all(
        row.student_id in {student.id for student in first.students} for row in first.appointments
    )
    assert {row.status for row in first.appointments} >= {
        "COMPLETED",
        "CANCELLED",
        "CHANGE_PENDING",
        "UPCOMING",
    }


def test_fixture_pending_proposals_match_pending_appointments():
    fixture = make_fixture_rows(
        seed=99,
        generated_at=datetime(2026, 11, 8, tzinfo=UTC),
        password_hash="fixture-hash",
    )
    pending_by_appointment = {
        proposal.appointment_id: proposal
        for proposal in fixture.proposals
        if proposal.status == "PENDING"
    }

    assert len(pending_by_appointment) == 10
    for appointment in fixture.appointments:
        if appointment.status == "CHANGE_PENDING":
            assert appointment.id in pending_by_appointment
        else:
            assert appointment.id not in pending_by_appointment


def test_cleanup_requires_matching_schema_and_persisted_run_marker():
    run_id = "b" * 32
    schema = f"prl_load_{run_id}"

    validate_cleanup_ownership(schema, run_id, run_id)

    for candidate in (
        ("production", run_id, run_id),
        (schema, "c" * 32, run_id),
        (schema, run_id, "d" * 32),
    ):
        with pytest.raises(LoadSafetyError):
            validate_cleanup_ownership(*candidate)
