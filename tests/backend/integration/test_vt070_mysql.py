"""Verify the new notice against real MySQL transactions and the inbox API."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from tests.backend.integration.test_staff_appointment_management import _seed, _staff_context

from app.api.notifications import require_notification_user
from app.dependencies.auth import require_csrf
from app.dependencies.staff import require_staff
from app.domain.auth import AuthContext
from app.models import Appointment, AuditEvent, Notification

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("fail_delivery", [False, True], ids=["TC14", "TC15"])
async def test_vt070_real_transactions_and_inbox(availability_env, monkeypatch, fail_delivery):
    env = availability_env
    appointment_id = UUID(int=70014)
    await _seed(
        env,
        appointment_id=appointment_id,
        staff_id=env.staff_id,
        starts_at=datetime(2026, 10, 19, 8, tzinfo=UTC),
    )
    staff = _staff_context(env.staff_id)
    student = AuthContext(
        UUID(int=70015),
        env.student_id,
        "student@example.invalid",
        "Test Student",
        "STUDENT",
        datetime(2030, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setitem(env.app.dependency_overrides, require_staff, lambda: staff)
    monkeypatch.setitem(env.app.dependency_overrides, require_csrf, lambda: staff)
    monkeypatch.setitem(env.app.dependency_overrides, require_notification_user, lambda: student)
    rejected = []
    engine = env.factory.kw["bind"].sync_engine

    def reject_new_notice(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and "APPOINTMENT_NO_SHOW" in str(
            parameters
        ):
            rejected.append(True)
            raise SQLAlchemyError("synthetic notice write failure")

    if fail_delivery:
        event.listen(engine, "before_cursor_execute", reject_new_notice)
    try:
        url = f"/api/v1/staff/me/appointments/{appointment_id}/outcome"
        first = await env.client.post(url, json={"status": "NO_SHOW"})
        replay = await env.client.post(url, json={"status": "NO_SHOW"})
    finally:
        if fail_delivery:
            event.remove(engine, "before_cursor_execute", reject_new_notice)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() and first.json()["status"] == "NO_SHOW"
    assert len(rejected) == int(fail_delivery)
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).status == "NO_SHOW"
        notices = list(
            await db.scalars(
                select(Notification).where(Notification.appointment_id == appointment_id)
            )
        )
        audit = list(
            await db.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(appointment_id)))
        )
    assert len(audit) == 1 and audit[0].action == "APPOINTMENT_OUTCOME_RECORDED"
    assert len(notices) == (2 if fail_delivery else 3)
    assert {n.recipient_id for n in notices if n.event_type == "APPOINTMENT_OUTCOME_RECORDED"} == {
        env.student_id,
        env.staff_id,
    }
    response = await env.client.get("/api/v1/notifications")
    assert response.status_code == 200
    added = [x for x in response.json()["items"] if x["event_type"] == "APPOINTMENT_NO_SHOW"]
    assert len(added) == (0 if fail_delivery else 1)
    if added:
        assert added[0]["appointment_id"] == str(appointment_id)
        assert added[0]["summary"] == "Lịch hẹn được ghi nhận vắng mặt"
