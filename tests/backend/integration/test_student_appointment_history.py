from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, event
from tests.backend.integration.test_appointments_api import (
    ORIGIN,
    assert_private,
    install_overrides,
    student_context,
)

from app.factory import create_app
from app.models import Appointment, Purpose, ServiceSetting, StaffPurpose, User


@pytest.mark.parametrize("role,status", [(None, 401), ("STAFF", 403), ("ADMIN", 403)])
async def test_history_requires_student(auth_settings, role, status):
    app = create_app(auth_settings)
    install_overrides(app, student_context(role) if role else None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get("/api/v1/appointments")
    assert response.status_code == status
    assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "query",
    [
        "page=1&page=2",
        "page_size=1&page_size=2",
        "status=UPCOMING&status=CANCELLED",
        "student_id=anything",
        "status=unknown",
        "page_size=101",
        "page=0",
    ],
)
async def test_history_rejects_invalid_and_duplicate_queries(auth_settings, query):
    app = create_app(auth_settings)
    install_overrides(app, student_context())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get("/api/v1/appointments?" + query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private(response, error_class="input")


async def test_history_pagination_ownership_and_inactive_catalog(availability_env):
    env = availability_env
    start = datetime(2026, 10, 16, 1, tzinfo=UTC)
    ids = [UUID(int=i) for i in range(1, 5)]
    async with env.factory.begin() as db:
        for i, appointment_id in enumerate(ids):
            db.add(
                Appointment(
                    id=appointment_id,
                    student_id=env.student_id if i < 3 else env.other_student_id,
                    staff_id=env.staff_id,
                    purpose_id=env.purpose_id,
                    duration_minutes=30,
                    starts_at=start + timedelta(days=1 if i == 0 else 0),
                    ends_at=start + timedelta(days=1 if i == 0 else 0, minutes=30),
                    status="CANCELLED" if i == 2 else "UPCOMING",
                    cancelled_at=start if i == 2 else None,
                    cancel_reason="Student cancellation" if i == 2 else None,
                    other_reason="Private description",
                    location="Room 2",
                    creation_request_key="history-" + str(i),
                    creation_request_hash=sha256(str(i).encode()).digest(),
                )
            )
        purpose = await db.get(Purpose, env.purpose_id)
        purpose.is_active = False
        purpose.duration_minutes = 60
        staff = await db.get(User, env.staff_id)
        staff.is_active = False
        association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        association.is_accepting = False
        await db.execute(delete(ServiceSetting))
    login = await env.client.post(
        "/api/v1/auth/login",
        json={
            "email": "student@example.invalid",
            "password": "  Good-password!  ",
        },
        headers={"Origin": ORIGIN},
    )
    assert login.status_code == 200
    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "appointments" in statement and statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = await env.client.get("/api/v1/appointments?page_size=2")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert first.status_code == 200
    assert len(statements) == 2
    body = first.json()
    assert (body["page"], body["page_size"], body["total"]) == (1, 2, 3)
    assert [item["id"] for item in body["items"]] == [str(ids[0]), str(ids[2])]
    item = body["items"][0]
    assert set(item) == {
        "id",
        "purpose_id",
        "purpose_code",
        "purpose_name",
        "staff_id",
        "staff_name",
        "duration_minutes",
        "starts_at",
        "ends_at",
        "location",
        "status",
        "created_at",
    }
    assert item["duration_minutes"] == 30
    assert (item["purpose_code"], item["purpose_name"], item["staff_name"]) == (
        "SELECTED",
        "Selected purpose",
        "Selected Staff",
    )
    second = await env.client.get("/api/v1/appointments?page=2&page_size=2")
    assert [item["id"] for item in second.json()["items"]] == [str(ids[1])]
    filtered = await env.client.get("/api/v1/appointments?status=CANCELLED&page_size=1")
    assert filtered.json()["total"] == 1
    assert [item["id"] for item in filtered.json()["items"]] == [str(ids[2])]
    empty = await env.client.get("/api/v1/appointments?status=NO_SHOW")
    assert empty.json()["items"] == [] and empty.json()["total"] == 0
    beyond = await env.client.get("/api/v1/appointments?page=99")
    assert beyond.json()["items"] == [] and beyond.json()["total"] == 3
    detail = await env.client.get("/api/v1/appointments/" + str(ids[2]))
    assert detail.status_code == 200
    assert detail.json()["cancelled_at"] == "2026-10-16T01:00:00Z"
    assert detail.json()["cancel_reason"] == "Student cancellation"
    assert detail.json()["description"] == "Private description"
    assert detail.json()["purpose_code"] == "SELECTED"
    assert detail.json()["staff_name"] == "Selected Staff"
    assert detail.json()["duration_minutes"] == 30
    hidden = await env.client.get("/api/v1/appointments/" + str(ids[3]))
    absent = await env.client.get("/api/v1/appointments/" + str(uuid4()))
    assert hidden.status_code == absent.status_code == 404
    assert hidden.json()["error"]["code"] == absent.json()["error"]["code"] == "NOT_FOUND"
    assert hidden.json()["error"]["message"] == absent.json()["error"]["message"]
    for response in (first, second, filtered, empty, beyond, detail):
        assert_private(response)
    for response in (hidden, absent):
        assert_private(response, error_class="input")
