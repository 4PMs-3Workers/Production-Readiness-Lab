"""VT-071 decision rules, real MySQL boundary and source replay workflow."""

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, func, select
from tests.backend.integration.test_appointments_api import (
    ORIGIN,
    assert_private,
    install_overrides,
    student_context,
)
from tests.backend.integration.test_booking_context import sign_in

from app.factory import create_app
from app.models import Appointment, Purpose, User
from app.schemas.appointments import AppointmentListQuery

PATH = "/api/v1/appointments/export"
COLUMNS = [
    "id",
    "purpose_code",
    "purpose_name",
    "staff_name",
    "starts_at",
    "ends_at",
    "duration_minutes",
    "status",
    "location",
]


def rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="appointments.csv"'
    assert_private(response)
    reader = csv.DictReader(io.StringIO(response.text))
    assert reader.fieldnames == COLUMNS
    return list(reader)


@pytest.mark.parametrize("role,status", [(None, 401), ("STAFF", 403), ("ADMIN", 403)])
async def test_export_denies_missing_session_and_wrong_role(auth_settings, role, status):
    """TC02/03/04: API dependency harness; does not prove session DB contract."""
    app = create_app(auth_settings)
    install_overrides(app, student_context(role) if role else None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        response = await client.get(PATH)
    assert response.status_code == status
    assert_private(response, error_class="input")
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize(
    "query",
    [
        "student_id=00000000-0000-0000-0000-000000000001",
        "page=1",
        "page_size=20",
        "status=unknown",
        "status=UPCOMING&status=CANCELLED",
    ],
)
async def test_export_rejects_scope_and_invalid_query(auth_settings, query):
    """TC06/07: forbid owner injection, paging and collapsed duplicate keys."""
    app = create_app(auth_settings)
    install_overrides(app, student_context())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        response = await client.get(PATH + "?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private(response, error_class="input")


async def seed_history(env, count=3):
    """Use real persisted schema, public joins and internal secrets with distinct values."""
    start = datetime(2026, 10, 16, 1, tzinfo=UTC)
    async with env.factory.begin() as db:
        for index in range(count):
            cancelled = index % 2 == 1
            db.add(
                Appointment(
                    id=UUID(int=100 + index),
                    student_id=env.student_id,
                    staff_id=env.staff_id,
                    purpose_id=env.purpose_id,
                    duration_minutes=30,
                    starts_at=start + timedelta(days=index),
                    ends_at=start + timedelta(days=index, minutes=30),
                    status="CANCELLED" if cancelled else "UPCOMING",
                    cancelled_at=start if cancelled else None,
                    location='Phòng "A", tầng 2\nViệt Nam',
                    other_reason="NEVER_EXPORT_DESCRIPTION",
                    creation_request_key=f"private-key-{index}",
                    creation_request_hash=bytes(32),
                )
            )
        db.add(
            Appointment(
                id=UUID(int=99999),
                student_id=env.other_student_id,
                staff_id=env.staff_id,
                purpose_id=env.purpose_id,
                duration_minutes=30,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                status="UPCOMING",
                location="FOREIGN_SECRET",
                creation_request_key="foreign-key",
                creation_request_hash=bytes(32),
            )
        )


async def test_export_real_login_scope_contract_and_inactive_catalog(availability_env):
    """TC01/05/11: real session, UUID/time adapters, owner SQL, joins and CSV parser."""
    env = availability_env
    await seed_history(env)
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        purpose.is_active = False
        purpose.name = "Tư vấn, học tập"
        staff = await db.get(User, env.staff_id)
        staff.is_active = False
    await sign_in(env)
    response = await env.client.get(PATH)
    exported = rows(response)
    async with env.factory() as db:
        expected = list(
            (
                await db.scalars(
                    select(Appointment.id)
                    .where(Appointment.student_id == env.student_id)
                    .order_by(Appointment.starts_at.desc(), Appointment.id.desc())
                )
            ).all()
        )
    assert [row["id"] for row in exported] == [str(value) for value in expected]
    assert exported[0]["starts_at"] == "2026-10-18T01:00:00Z"
    assert exported[0]["duration_minutes"] == "30"
    assert exported[0]["purpose_name"] == "Tư vấn, học tập"
    assert exported[0]["location"] == 'Phòng "A", tầng 2\nViệt Nam'
    for secret in [
        "FOREIGN_SECRET",
        "NEVER_EXPORT_DESCRIPTION",
        "private-key",
        "creation_request_hash",
    ]:
        assert secret not in response.text


async def test_export_status_filter_and_empty_history(availability_env):
    """TC08/09: header-only empty, then independent status oracle."""
    env = availability_env
    await sign_in(env)
    assert rows(await env.client.get(PATH)) == []
    await seed_history(env)
    exported = rows(await env.client.get(PATH + "?status=CANCELLED"))
    assert [row["id"] for row in exported] == [str(UUID(int=101))]
    assert all(row["status"] == "CANCELLED" for row in exported)


async def test_export_rejects_overflow_instead_of_truncation(availability_env):
    """TC10: real SQL row bound, both sides of boundary."""
    env = availability_env
    await seed_history(env, 1000)
    await sign_in(env)
    assert len(rows(await env.client.get(PATH))) == 1000
    async with env.factory.begin() as db:
        sample = await db.get(Appointment, UUID(int=100))
        db.add(
            Appointment(
                student_id=env.student_id,
                staff_id=env.staff_id,
                purpose_id=env.purpose_id,
                duration_minutes=30,
                starts_at=sample.starts_at,
                ends_at=sample.ends_at,
                status="UPCOMING",
                creation_request_key="overflow",
                creation_request_hash=bytes(32),
            )
        )
    response = await env.client.get(PATH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPORT_TOO_LARGE"
    assert "content-disposition" not in response.headers
    assert_private(response, error_class="business")


async def test_mock_hides_real_database_contract_failure(availability_env, monkeypatch):
    """TC12: plausible row mock passes, actual invalid column fails as sanitized 503."""
    from app.repositories import appointments as repository

    env = availability_env
    await seed_history(env)
    await sign_in(env)
    assert len(rows(await env.client.get(PATH))) == 3
    async with env.factory() as db:
        good, _ = await repository.read_student_appointment_page(
            db,
            AppointmentListQuery(),
            student_id=env.student_id,
        )

    async def fake_rows(*args, **kwargs):
        return good

    with monkeypatch.context() as patch:
        patch.setattr(repository, "read_student_appointment_export", fake_rows)
        assert len(rows(await env.client.get(PATH))) == 3

    engine = env.factory.kw["bind"].sync_engine

    def invalid_column(conn, cursor, statement, parameters, context, executemany):
        if "appointments" in statement and "JOIN purposes" in statement:
            return (
                statement.replace("appointments.id", "appointments.vt071_missing_column"),
                parameters,
            )
        return statement, parameters

    event.listen(engine, "before_cursor_execute", invalid_column, retval=True)
    try:
        response = await env.client.get(PATH)
    finally:
        event.remove(engine, "before_cursor_execute", invalid_column)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "vt071_missing_column" not in response.text
    assert "SELECT" not in response.text
    assert_private(response, error_class="service")
    # Rollback/connection cleanup: a new request after removing fault must work.
    assert len(rows(await env.client.get(PATH))) == 3


async def test_booking_replay_after_cancellation_exports_one_source_row(booking_env):
    """TC13: mutation oracle uses existing real booking replay guard, no fake GET guard."""
    env, _, starts_at = booking_env
    await sign_in(env)
    csrf = env.client.cookies.get("prl_csrf")
    payload = {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(env.staff_id),
        "starts_at": starts_at.isoformat(),
    }
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": "vt071-source-retry"}
    first = await env.client.post("/api/v1/appointments", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    appointment_id = first.json()["id"]
    cancelled = await env.client.post(
        f"/api/v1/appointments/{appointment_id}/cancel", json={}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    replay = await env.client.post("/api/v1/appointments", json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    exported = rows(await env.client.get(PATH))
    assert [(row["id"], row["status"]) for row in exported] == [(appointment_id, "CANCELLED")]
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Appointment)) == 1


async def test_export_without_real_session_is_denied(availability_env):
    """TC14: no authentication override; verifies real missing-session path."""
    response = await availability_env.client.get(PATH)
    assert response.status_code == 401
    assert "content-disposition" not in response.headers
    assert_private(response, error_class="input")
