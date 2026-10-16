"""Booking rollback, cancellation, and lost-response recovery over MySQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.domain.auth import AuthContext
from app.models import Appointment, AuditEvent, Notification
from app.schemas.appointments import AppointmentCreateRequest
from app.services import booking

ORIGIN = "http://localhost:3000"


async def _login(env):
    response = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def _payload(env, starts_at):
    return {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(env.staff_id),
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
    }


async def _assert_no_creation_rows(factory):
    async with factory() as db:
        for model in (Appointment, AuditEvent, Notification):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_notification_insert_failure_rolls_back_http_booking_bundle(booking_env):
    env, _, starts_at = booking_env
    await _login(env)
    trigger = "reject_booking_http_notification"
    async with env.factory.kw["bind"].begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON notifications "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'injected failure'"
        )
    try:
        response = await env.client.post(
            "/api/v1/appointments",
            json=_payload(env, starts_at),
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": env.client.cookies.get("prl_csrf"),
                "Idempotency-Key": "rollback-before-commit-http",
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert "injected failure" not in response.text
        await _assert_no_creation_rows(env.factory)
    finally:
        async with env.factory.kw["bind"].begin() as connection:
            await connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")


class _LoseFirstResponseTransport(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self._inner = httpx.ASGITransport(app=app)
        self._lost = False

    async def handle_async_request(self, request):
        response = await self._inner.handle_async_request(request)
        if not self._lost:
            self._lost = True
            await response.aread()
            await response.aclose()
            raise httpx.ReadError(
                "synthetic response loss after server completion", request=request
            )
        return response

    async def aclose(self):
        await self._inner.aclose()


async def test_committed_booking_with_lost_http_response_replays_same_key_and_body(booking_env):
    env, _, starts_at = booking_env
    await _login(env)
    transport = _LoseFirstResponseTransport(env.app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        client.cookies.update(env.client.cookies)
        csrf = client.cookies.get("prl_csrf")
        payload = _payload(env, starts_at)
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "response-lost-after-commit",
        }
        with pytest.raises(httpx.ReadError):
            await client.post("/api/v1/appointments", json=payload, headers=headers)

        replay = await client.post("/api/v1/appointments", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json()["status"] == "UPCOMING"

    async with env.factory() as db:
        appointments = list((await db.scalars(select(Appointment))).all())
        assert len(appointments) == 1
        assert appointments[0].starts_at == starts_at
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert await db.scalar(select(func.count()).select_from(Notification)) == 2


async def test_cancelled_service_request_rolls_back_staged_bundle(
    booking_env, auth_settings, monkeypatch
):
    env, _, starts_at = booking_env
    payload = AppointmentCreateRequest(
        purpose_id=env.purpose_id,
        staff_id=env.staff_id,
        starts_at=starts_at,
    )
    context = AuthContext(
        session_id=uuid4(),
        user_id=env.student_id,
        email="student@example.invalid",
        name="Test Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    staged = asyncio.Event()
    add_bundle = booking.appointments.add_creation_bundle

    async def stage_then_wait(db, **rows):
        await add_bundle(db, **rows)
        staged.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(booking.appointments, "add_creation_bundle", stage_then_wait)

    async def submit():
        async with env.factory() as db:
            return await booking.create_appointment(
                db,
                payload,
                context=context,
                idempotency_key="cancel-before-commit",
                request_id="cancel-test",
                settings=auth_settings,
                deadline=asyncio.get_running_loop().time() + 10,
            )

    task = asyncio.create_task(submit())
    await asyncio.wait_for(staged.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_no_creation_rows(env.factory)
