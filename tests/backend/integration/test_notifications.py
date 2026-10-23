"""MySQL 8.4 acceptance checks for personal inbox isolation and durable read state."""

import asyncio
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, text

from app.core.auth_cookies import CSRF_COOKIE
from app.core.csrf import issue_csrf_token
from app.dependencies.auth import require_auth
from app.domain.auth import AuthContext
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    StaffPurpose,
    User,
)
from app.repositories import notifications as notification_repository

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
                ChangeProposal(
                    appointment_id=appointment_id,
                    created_by=staff_id,
                    created_at=STAMP - timedelta(days=1),
                    proposed_starts_at=STAMP + timedelta(days=2),
                    proposed_ends_at=STAMP + timedelta(days=2, minutes=30),
                    status="EXPIRED",
                    expires_at=STAMP - timedelta(hours=1),
                    resolved_at=STAMP - timedelta(hours=1),
                ),
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

    async with factory() as db:
        baseline_audits = await db.scalar(select(func.count()).select_from(AuditEvent))
        baseline_notifications = await db.scalar(select(func.count()).select_from(Notification))
        baseline_appointment = await db.scalar(
            select(Appointment.status).where(Appointment.id == appointment_id)
        )
        baseline_proposals = (
            await db.execute(
                select(ChangeProposal.id, ChangeProposal.status, ChangeProposal.resolved_at).where(
                    ChangeProposal.appointment_id == appointment_id
                )
            )
        ).all()
    assert len(baseline_proposals) == 1

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
        notification_count = await db.scalar(select(func.count()).select_from(Notification))
        appointment_status = await db.scalar(
            select(Appointment.status).where(Appointment.id == appointment_id)
        )
        proposals = (
            await db.execute(
                select(ChangeProposal.id, ChangeProposal.status, ChangeProposal.resolved_at).where(
                    ChangeProposal.appointment_id == appointment_id
                )
            )
        ).all()
    assert persisted == datetime.fromisoformat(first.json()["read_at"].replace("Z", "+00:00"))
    assert own_count == 3
    assert appointment_status == baseline_appointment == "UPCOMING"
    assert proposals == baseline_proposals
    assert notification_count == baseline_notifications
    assert audit_count == baseline_audits

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


@pytest.mark.booking_stress
async def test_recipient_query_plans_and_concurrent_read_with_large_history(notification_env):
    client, factory, context, appointment_id, _, staff_id, _ = notification_env
    # A busy student and staff member each have 5,000 historical events; 80% are read.
    async with factory.begin() as db:
        for recipient_id in (context.user_id, staff_id):
            for start in range(0, 5000, 500):
                rows = [
                    {
                        "id": uuid4(),
                        "recipient_id": recipient_id,
                        "appointment_id": appointment_id,
                        "event_type": "APPOINTMENT_CREATED",
                        "created_at": STAMP + timedelta(seconds=position),
                        "read_at": STAMP + timedelta(days=1) if position % 5 else None,
                    }
                    for position in range(start, start + 500)
                ]
                await db.execute(insert(Notification), rows)

    async with factory() as db:
        await db.execute(text("ANALYZE TABLE notifications"))
        await db.commit()
        for unread_only in (False, True):
            condition = "AND n.read_at IS NULL" if unread_only else ""
            statement = text(
                "EXPLAIN SELECT n.*, a.student_id, a.staff_id, a.starts_at, a.status "
                "FROM notifications AS n LEFT JOIN appointments AS a "
                "ON a.id = n.appointment_id WHERE n.recipient_id = :recipient "
                f"{condition} ORDER BY n.created_at DESC, n.id DESC LIMIT 20"
            )
            plan = [
                dict(row._mapping)
                for row in (
                    await db.execute(
                        statement,
                        {
                            "recipient": context.user_id.bytes,
                        },
                    )
                ).all()
            ]
            notification_step = next(row for row in plan if row["table"] == "n")
            print(f"notification plan unread_only={unread_only}: {notification_step}")
            assert notification_step["key"] == "ix_notifications_recipient_history"
            assert notification_step["type"] in {"ref", "range"}
            count_plan = (
                await db.execute(
                    text(
                        "EXPLAIN SELECT COUNT(*) FROM notifications AS n "
                        f"WHERE n.recipient_id = :recipient {condition}"
                    ),
                    {"recipient": context.user_id.bytes},
                )
            ).one()
            print(f"notification count plan unread_only={unread_only}: {dict(count_plan._mapping)}")

            durations = []
            for _ in range(10):
                started = perf_counter()
                records, total = await notification_repository.read_notification_page(
                    db,
                    recipient_id=context.user_id,
                    page=1,
                    page_size=20,
                    unread_only=unread_only,
                )
                durations.append((perf_counter() - started) * 1000)
                assert len(records) == 20
                assert total >= 1000
            print(
                f"notification page unread_only={unread_only}: "
                f"max={max(durations):.1f}ms over 10 requests"
            )

    notification_id = UUID("00000000-0000-0000-0000-000000000001")
    started = perf_counter()
    responses = await asyncio.gather(
        *(client.post(f"/api/v1/notifications/{notification_id}/read", json={}) for _ in range(8))
    )
    elapsed = (perf_counter() - started) * 1000
    print(f"eight concurrent reads of one notification: {elapsed:.1f}ms")
    assert {response.status_code for response in responses} == {200}
    assert len({response.json()["read_at"] for response in responses}) == 1

    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Notification)) == 10005
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert (
            await db.scalar(select(Appointment.status).where(Appointment.id == appointment_id))
            == "UPCOMING"
        )


async def _count_audits(factory):
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(AuditEvent))
