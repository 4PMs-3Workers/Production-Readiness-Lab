"""Provider-to-consumer contract for changing the student booking quota."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from tests.backend.integration.test_admin_account_endpoints import ORIGIN, write_headers

from app.models import Appointment, AuditEvent, Notification, ServiceSetting, User

pytestmark = pytest.mark.mysql
CONFIGURATION = "/api/v1/admin/booking-configuration"
BOOKINGS = "/api/v1/appointments"
PASSWORD = "  Good-password!  "


def policy_body(configuration, **changes):
    return {
        "expected_updated_at": configuration["updated_at"],
        "timezone": configuration["timezone"],
        "start_step_minutes": configuration["start_step_minutes"],
        "booking_horizon_days": configuration["booking_horizon_days"],
        "max_active_appointments": configuration["max_active_appointments"],
        "windows": configuration["windows"],
        **changes,
    }


async def row_count(factory, model, *conditions):
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


async def sign_in_student(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.json()


async def test_admin_policy_change_controls_real_booking_outcome_and_atomic_effects(booking_env):
    env, _, starts_at = booking_env
    async with env.factory.begin() as db:
        student = await db.get(User, env.student_id)
        assert student is not None
        admin_id = uuid4()
        db.add(
            User(
                id=admin_id,
                email="quota-admin@example.invalid",
                name="Quota Admin",
                role="ADMIN",
                password_hash=student.password_hash,
            )
        )
        db.add_all(
            [
                Appointment(
                    student_id=env.student_id,
                    staff_id=env.staff_id,
                    purpose_id=env.purpose_id,
                    duration_minutes=30,
                    starts_at=starts_at + timedelta(days=10 + offset),
                    ends_at=starts_at + timedelta(days=10 + offset, minutes=30),
                    status=status,
                    creation_request_key=f"existing-quota-{offset}",
                    creation_request_hash=bytes(32),
                )
                for offset, status in enumerate(("UPCOMING", "CHANGE_PENDING"))
            ]
        )

    admin = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://localhost:3000"
    )
    student = env.client
    try:
        admin_login = await admin.post(
            "/api/v1/auth/login",
            json={"email": "quota-admin@example.invalid", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert admin_login.status_code == 200, admin_login.json()
        await sign_in_student(student)

        current = (await admin.get(CONFIGURATION)).json()
        lowered = await admin.put(
            CONFIGURATION,
            json=policy_body(current, max_active_appointments=2),
            headers=write_headers(admin),
        )
        assert lowered.status_code == 200, lowered.json()
        assert lowered.json()["max_active_appointments"] == 2
        assert (await admin.get(CONFIGURATION)).json() == lowered.json()

        body = {
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        }
        denied = await student.post(
            BOOKINGS,
            json=body,
            headers={
                **write_headers(student),
                "Idempotency-Key": "quota-blocked-at-two",
            },
        )
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "BOOKING_QUOTA_EXCEEDED"
        assert await row_count(env.factory, Appointment) == 2
        assert (
            await row_count(env.factory, AuditEvent, AuditEvent.action == "APPOINTMENT_CREATED")
            == 0
        )
        assert await row_count(env.factory, Notification) == 0

        current = lowered.json()
        raised = await admin.put(
            CONFIGURATION,
            json=policy_body(current, max_active_appointments=3),
            headers=write_headers(admin),
        )
        assert raised.status_code == 200, raised.json()
        assert raised.json()["max_active_appointments"] == 3

        accepted = await student.post(
            BOOKINGS,
            json=body,
            headers={
                **write_headers(student),
                "Idempotency-Key": "quota-accepted-at-three",
            },
        )
        assert accepted.status_code == 201, accepted.json()
        created_id = accepted.json()["id"]
        assert accepted.json()["status"] == "UPCOMING"
        assert await row_count(env.factory, Appointment) == 3
        assert (
            await row_count(
                env.factory,
                AuditEvent,
                AuditEvent.action == "APPOINTMENT_CREATED",
                AuditEvent.entity_id == created_id,
            )
            == 1
        )
        assert (
            await row_count(env.factory, Notification, Notification.appointment_id == created_id)
            == 2
        )
    finally:
        await admin.aclose()


async def test_booking_config_read_failure_is_not_replaced_by_default_policy(
    booking_env, monkeypatch
):
    env, _, starts_at = booking_env
    await sign_in_student(env.client)

    async def fail_read(_db):
        raise SQLAlchemyError("synthetic service_settings read failure")

    monkeypatch.setattr("app.repositories.appointments.lock_service_settings", fail_read)
    response = await env.client.post(
        BOOKINGS,
        json={
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        },
        headers={
            **write_headers(env.client),
            "Idempotency-Key": "quota-config-read-failure",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert await row_count(env.factory, Appointment) == 0
    assert await row_count(env.factory, AuditEvent) == 0
    assert await row_count(env.factory, Notification) == 0


async def test_missing_booking_configuration_is_not_replaced_by_default_policy(booking_env):
    env, _, starts_at = booking_env
    await sign_in_student(env.client)
    async with env.factory.begin() as db:
        await db.execute(delete(ServiceSetting).where(ServiceSetting.id == 1))

    response = await env.client.post(
        BOOKINGS,
        json={
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        },
        headers={
            **write_headers(env.client),
            "Idempotency-Key": "quota-config-missing",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert await row_count(env.factory, Appointment) == 0
    assert await row_count(env.factory, AuditEvent) == 0
    assert await row_count(env.factory, Notification) == 0
