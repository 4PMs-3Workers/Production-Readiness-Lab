"""MySQL 8.4 acceptance checks for personal inbox isolation and durable read state."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import func, select

from app.core.auth_cookies import CSRF_COOKIE
from app.core.csrf import issue_csrf_token
from app.dependencies.auth import require_auth
from app.domain.auth import AuthContext
from app.models import (
    Appointment,
    AuditEvent,
    Notification,
    Purpose,
    StaffPurpose,
    User,
)

ORIGIN = "http://localhost:3000"
STAMP = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def notification_env(auth_env):
    client, factory, student_id, app = auth_env
    staff_id, other_student_id, purpose_id, appointment_id = (uuid4() for _ in range(4))
    async with factory.begin() as db:
        student = await db.get(User, student_id)
        assert student is not None
        db.add_all(
            [
                User(
                    id=staff_id,
                    email="staff-notifications@example.invalid",
                    name="Test Staff",
                    role="STAFF",
                    password_hash="unused-test-hash",
                ),
                User(
                    id=other_student_id,
                    email="other-notifications@example.invalid",
                    name="Other Student",
                    role="STUDENT",
                    password_hash=student.password_hash,
                ),
                Purpose(
                    id=purpose_id,
                    code="NOTIFICATION_TEST",
                    name="Notification test purpose",
                    duration_minutes=30,
                ),
            ]
        )
        await db.flush()
        db.add(StaffPurpose(staff_id=staff_id, purpose_id=purpose_id))
        await db.flush()
        appointment = Appointment(
            id=appointment_id,
            student_id=student_id,
            staff_id=staff_id,
            purpose_id=purpose_id,
            duration_minutes=30,
            other_reason="PRIVATE CONSULTATION TEXT",
            starts_at=STAMP + timedelta(days=1),
            ends_at=STAMP + timedelta(days=1, minutes=30),
            location="Room 4",
            status="UPCOMING",
            creation_request_key="notification-fixture-key",
            creation_request_hash=b"n" * 32,
        )
        db.add(appointment)
        await db.flush()
        db.add_all(
            [
                Notification(
                    id=UUID("00000000-0000-0000-0000-000000000001"),
                    recipient_id=student_id,
                    appointment_id=appointment_id,
                    event_type="APPOINTMENT_CREATED",
                    created_at=STAMP,
                ),
                Notification(
                    id=UUID("00000000-0000-0000-0000-000000000002"),
                    recipient_id=student_id,
                    appointment_id=appointment_id,
                    event_type="CHANGE_PROPOSAL_EXPIRED",
                    created_at=STAMP,
                ),
                Notification(
                    recipient_id=student_id,
                    appointment_id=appointment_id,
                    event_type="FUTURE_UNKNOWN_EVENT",
                    created_at=STAMP - timedelta(minutes=1),
                ),
                Notification(
                    recipient_id=other_student_id,
                    appointment_id=appointment_id,
                    event_type="APPOINTMENT_CREATED",
                ),
                Notification(
                    recipient_id=staff_id,
                    appointment_id=appointment_id,
                    event_type="APPOINTMENT_CREATED",
                ),
            ]
        )

    context = AuthContext(
        session_id=uuid4(),
        user_id=student_id,
        email="student@example.invalid",
        name="Test Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def authenticated_context():
        return context

    app.dependency_overrides[require_auth] = authenticated_context
    token = issue_csrf_token(context.session_id, "test-only-csrf-secret")
    client.cookies.set(CSRF_COOKIE, token)
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": token})
    return client, factory, context, appointment_id, other_student_id, staff_id, app


async def test_list_is_recipient_scoped_unread_filtered_and_stably_ordered(notification_env):
    client, _, _, _, other_student_id, _, app = notification_env

    unread = await client.get("/api/v1/notifications?page=1&page_size=1&unread_only=true")
    assert unread.status_code == 200
    assert unread.headers["Cache-Control"] == "private, no-store"
    assert unread.json()["total"] == 3
    assert unread.json()["items"][0]["id"] == "00000000-0000-0000-0000-000000000002"

    next_page = await client.get("/api/v1/notifications?page=2&page_size=1&unread_only=true")
    assert next_page.json()["items"][0]["id"] == "00000000-0000-0000-0000-000000000001"

    all_items = await client.get("/api/v1/notifications?page=1&page_size=20")
    body = all_items.json()
    assert body["total"] == 3
    unknown = next(item for item in body["items"] if item["event_type"] == "FUTURE_UNKNOWN_EVENT")
    assert unknown["summary"] == "Bạn có một thông báo mới"
    assert unknown["appointment_id"] is None
    assert "PRIVATE CONSULTATION TEXT" not in all_items.text
    assert all(item["id"] != "" for item in body["items"])

    beyond_end = await client.get("/api/v1/notifications?page=1000000&page_size=100")
    assert beyond_end.status_code == 200
    assert beyond_end.json()["items"] == []
    assert beyond_end.json()["total"] == 3

    other_context = AuthContext(
        session_id=uuid4(),
        user_id=other_student_id,
        email="other-notifications@example.invalid",
        name="Other Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def authenticate_other():
        return other_context

    app.dependency_overrides[require_auth] = authenticate_other
    other_inbox = await client.get("/api/v1/notifications")
    assert other_inbox.status_code == 200
    assert other_inbox.json()["total"] == 1
    assert other_inbox.json()["items"][0]["summary"] == "Bạn có một thông báo mới"
    assert other_inbox.json()["items"][0]["appointment_id"] is None


async def test_mark_read_is_idempotent_private_and_hides_foreign_ids(notification_env):
    client, factory, context, appointment_id, other_student_id, _, _ = notification_env
    notification_id = UUID("00000000-0000-0000-0000-000000000001")

    first, concurrent = await asyncio.gather(
        client.post(f"/api/v1/notifications/{notification_id}/read", json={}),
        client.post(f"/api/v1/notifications/{notification_id}/read", json={}),
    )
    second = await client.post(f"/api/v1/notifications/{notification_id}/read", json={})
    assert first.status_code == concurrent.status_code == second.status_code == 200
    assert first.headers["Cache-Control"] == second.headers["Cache-Control"] == "private, no-store"
    assert first.json()["read_at"] == concurrent.json()["read_at"] == second.json()["read_at"]

    async with factory() as db:
        persisted = await db.scalar(
            select(Notification.read_at).where(Notification.id == notification_id)
        )
        own_count = await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_id == context.user_id)
        )
        foreign_id = await db.scalar(
            select(Notification.id).where(Notification.recipient_id == other_student_id)
        )
        audit_count = await db.scalar(select(func.count()).select_from(AuditEvent))
        appointment_status = await db.scalar(
            select(Appointment.status).where(Appointment.id == appointment_id)
        )
    assert persisted == datetime.fromisoformat(first.json()["read_at"].replace("Z", "+00:00"))
    assert own_count == 3
    assert appointment_status == "UPCOMING"

    foreign = await client.post(f"/api/v1/notifications/{foreign_id}/read", json={})
    missing = await client.post(f"/api/v1/notifications/{uuid4()}/read", json={})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert foreign.status_code == 404
    assert audit_count == await _count_audits(factory)


async def test_staff_can_read_assigned_inbox_but_admin_has_no_implicit_access(notification_env):
    client, _, student_context, _, _, staff_id, app = notification_env
    staff_context = AuthContext(
        session_id=uuid4(),
        user_id=staff_id,
        email="staff-notifications@example.invalid",
        name="Test Staff",
        role="STAFF",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def authenticate_staff():
        return staff_context

    app.dependency_overrides[require_auth] = authenticate_staff
    staff_response = await client.get("/api/v1/notifications")
    assert staff_response.status_code == 200
    assert staff_response.json()["items"][0]["appointment_id"] is not None

    admin_context = AuthContext(
        session_id=uuid4(),
        user_id=student_context.user_id,
        email=student_context.email,
        name=student_context.name,
        role="ADMIN",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def authenticate_admin():
        return admin_context

    app.dependency_overrides[require_auth] = authenticate_admin
    admin_response = await client.get("/api/v1/notifications")
    assert admin_response.status_code == 403
    admin_token = issue_csrf_token(admin_context.session_id, "test-only-csrf-secret")
    client.cookies.set(CSRF_COOKIE, admin_token)
    client.headers["X-CSRF-Token"] = admin_token
    admin_read = await client.post(
        "/api/v1/notifications/00000000-0000-0000-0000-000000000001/read", json={}
    )
    assert admin_read.status_code == 403
    assert admin_read.json()["error"]["code"] == "FORBIDDEN_ROLE"


async def _count_audits(factory):
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(AuditEvent))
