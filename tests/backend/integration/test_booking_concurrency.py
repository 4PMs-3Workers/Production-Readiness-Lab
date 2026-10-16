"""End-to-end booking races over independent MySQL sessions and HTTP cookie jars."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select

from app.core.csrf import issue_csrf_token
from app.core.session_tokens import hash_session_token, new_session_token
from app.models import Appointment, AuditEvent, Notification, Session, User

ORIGIN = "http://localhost:3000"


async def _clients(booking_env, student_ids):
    """Create one persisted auth session and one independent cookie jar per client."""
    env, _, _ = booking_env
    settings = env.app.state.settings
    session_rows = []
    client_data = []
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    for student_id in student_ids:
        session_id = uuid4()
        token = new_session_token()
        csrf = issue_csrf_token(session_id, settings.csrf_secret.get_secret_value())
        session_rows.append(
            Session(
                id=session_id,
                user_id=student_id,
                token_hash=hash_session_token(token),
                expires_at=expires_at,
            )
        )
        client_data.append((token, csrf))
    async with env.factory.begin() as db:
        db.add_all(session_rows)

    clients = []
    for token, csrf in client_data:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        client.cookies.set("prl_session", token)
        client.cookies.set("prl_csrf", csrf)
        clients.append((client, csrf))
    return clients


async def _distinct_student_ids(booking_env, count):
    env, _, _ = booking_env
    ids = [env.student_id, env.other_student_id]
    if count <= len(ids):
        return ids[:count]
    async with env.factory() as db:
        template = await db.get(User, env.student_id)
        assert template is not None
        password_hash = template.password_hash
    new_ids = [uuid4() for _ in range(count - len(ids))]
    async with env.factory.begin() as db:
        db.add_all(
            [
                User(
                    id=user_id,
                    email=f"race-student-{index}@example.invalid",
                    name=f"Race student {index}",
                    role="STUDENT",
                    password_hash=password_hash,
                    is_active=True,
                )
                for index, user_id in enumerate(new_ids, start=2)
            ]
        )
    return ids + new_ids


def _booking_headers(csrf, key):
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf,
        "Idempotency-Key": key,
    }


def _payload(env, starts_at, *, staff_id=None):
    return {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(staff_id or env.staff_id),
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
    }


def _allow_bounded_stress_budget(env):
    """Give only these integration requests a bounded, stress-appropriate deadline."""
    env.app.state.settings = env.app.state.settings.model_copy(
        update={
            "request_timeout_seconds": 20.0,
            "db_query_timeout_seconds": 10.0,
            "db_lock_timeout_seconds": 5.0,
        }
    )


async def _assert_created_bundle(factory, *, starts_at, expected_count=1):
    """Read committed invariants through a new session after all HTTP calls finish."""
    async with factory() as db:
        appointments = list(
            (await db.scalars(select(Appointment).where(Appointment.starts_at == starts_at))).all()
        )
        assert len(appointments) == expected_count
        appointment_ids = [row.id for row in appointments]
        audit_count = int(
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.entity_id.in_([str(value) for value in appointment_ids]))
            )
            or 0
        )
        notification_count = int(
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.appointment_id.in_(appointment_ids))
            )
            or 0
        )
        assert audit_count == expected_count
        assert notification_count == 2 * expected_count
        for row in appointments:
            recipients = set(
                (
                    await db.scalars(
                        select(Notification.recipient_id).where(
                            Notification.appointment_id == row.id
                        )
                    )
                ).all()
            )
            assert recipients == {row.student_id, row.staff_id}


async def _close_clients(clients):
    await asyncio.gather(*(client.aclose() for client, _ in clients))


async def test_fifty_students_racing_for_one_staff_slot_create_exactly_one_bundle(
    booking_env,
):
    env, _, starts_at = booking_env
    _allow_bounded_stress_budget(env)
    clients = await _clients(booking_env, await _distinct_student_ids(booking_env, 50))
    barrier = asyncio.Barrier(50)
    payload = _payload(env, starts_at)

    async def submit(index, client_and_csrf):
        client, csrf = client_and_csrf
        await barrier.wait()
        return await client.post(
            "/api/v1/appointments",
            json=payload,
            headers=_booking_headers(csrf, f"staff-slot-race-{index}"),
        )

    try:
        responses = await asyncio.gather(
            *(submit(index, item) for index, item in enumerate(clients))
        )
        assert sum(response.status_code == 201 for response in responses) == 1
        conflicts = [response for response in responses if response.status_code == 409]
        assert len(conflicts) == 49
        assert all(response.json()["error"]["code"] == "BOOKING_CONFLICT" for response in conflicts)
        assert all(response.status_code in {201, 409} for response in responses)
        await _assert_created_bundle(env.factory, starts_at=starts_at)
        async with env.factory() as db:
            assert await db.scalar(select(func.count()).select_from(Appointment)) == 1
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
            assert await db.scalar(select(func.count()).select_from(Notification)) == 2
    finally:
        await _close_clients(clients)


async def test_fifty_sessions_for_one_student_serialize_quota_at_three(booking_env):
    env, _, starts_at = booking_env
    _allow_bounded_stress_budget(env)
    for offset in (2, 3):
        await _seed_existing_appointment(
            env,
            student_id=env.student_id,
            staff_id=env.other_staff_id,
            starts_at=starts_at + timedelta(days=offset),
            key=f"preexisting-quota-{offset}",
        )
    clients = await _clients(booking_env, [env.student_id] * 50)
    barrier = asyncio.Barrier(50)
    payload = _payload(env, starts_at)

    async def submit(index, client_and_csrf):
        client, csrf = client_and_csrf
        await barrier.wait()
        return await client.post(
            "/api/v1/appointments",
            json=payload,
            headers=_booking_headers(csrf, f"student-quota-race-{index}"),
        )

    try:
        responses = await asyncio.gather(
            *(submit(index, item) for index, item in enumerate(clients))
        )
        assert sum(response.status_code == 201 for response in responses) == 1
        quota_errors = [response for response in responses if response.status_code == 409]
        assert len(quota_errors) == 49
        assert all(
            response.json()["error"]["code"] == "BOOKING_QUOTA_EXCEEDED"
            for response in quota_errors
        )
        assert all(response.status_code in {201, 409} for response in responses)
        async with env.factory() as db:
            student_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(Appointment)
                    .where(Appointment.student_id == env.student_id)
                )
                or 0
            )
            assert student_count == 3
        await _assert_created_bundle(env.factory, starts_at=starts_at)
    finally:
        await _close_clients(clients)


async def test_same_student_overlapping_requests_with_different_staff_conflict(booking_env):
    env, _, starts_at = booking_env
    clients = await _clients(booking_env, [env.student_id, env.student_id])
    barrier = asyncio.Barrier(2)
    staff_ids = (env.staff_id, env.other_staff_id)

    async def submit(index):
        client, csrf = clients[index]
        await barrier.wait()
        return await client.post(
            "/api/v1/appointments",
            json=_payload(env, starts_at, staff_id=staff_ids[index]),
            headers=_booking_headers(csrf, f"different-staff-overlap-{index}"),
        )

    try:
        responses = await asyncio.gather(submit(0), submit(1))
        assert sorted(response.status_code for response in responses) == [201, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["error"]["code"] == "BOOKING_CONFLICT"
        assert all(response.status_code in {201, 409} for response in responses)
        await _assert_created_bundle(env.factory, starts_at=starts_at)
    finally:
        await _close_clients(clients)


async def test_slot_invalidated_after_preview_is_rejected(booking_env):
    from app.models import StaffUnavailability

    env, selected_date, starts_at = booking_env
    clients = await _clients(booking_env, [env.student_id])
    client, csrf = clients[0]
    try:
        preview = await client.get(
            "/api/v1/availability",
            params={
                "purpose_id": str(env.purpose_id),
                "staff_id": str(env.staff_id),
                "date": selected_date.isoformat(),
            },
        )
        assert preview.status_code == 200
        assert any(
            slot["starts_at"] == starts_at.isoformat().replace("+00:00", "Z")
            for slot in preview.json()["slots"]
        )

        async with env.factory.begin() as db:
            db.add(
                StaffUnavailability(
                    staff_id=env.staff_id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=30),
                )
            )
        response = await client.post(
            "/api/v1/appointments",
            json=_payload(env, starts_at),
            headers=_booking_headers(csrf, "preview-became-stale"),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BOOKING_CONFLICT"
        async with env.factory() as db:
            assert await db.scalar(select(func.count()).select_from(Appointment)) == 0
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
            assert await db.scalar(select(func.count()).select_from(Notification)) == 0
    finally:
        await _close_clients(clients)


async def test_concurrent_same_key_replays_one_immutable_receipt(booking_env):
    env, _, starts_at = booking_env
    clients = await _clients(booking_env, [env.student_id, env.student_id])
    barrier = asyncio.Barrier(2)
    payload = _payload(env, starts_at)

    async def submit(index):
        client, csrf = clients[index]
        await barrier.wait()
        return await client.post(
            "/api/v1/appointments",
            json=payload,
            headers=_booking_headers(csrf, "same-key-contention"),
        )

    try:
        responses = await asyncio.gather(submit(0), submit(1))
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert responses[0].json() == responses[1].json()
        await _assert_created_bundle(env.factory, starts_at=starts_at)
    finally:
        await _close_clients(clients)


async def test_http_replay_after_cancellation_returns_original_receipt(booking_env):
    env, _, starts_at = booking_env
    clients = await _clients(booking_env, [env.student_id])
    client, csrf = clients[0]
    payload = _payload(env, starts_at)
    key = "replay-after-state-change"
    headers = _booking_headers(csrf, key)
    try:
        created = await client.post("/api/v1/appointments", json=payload, headers=headers)
        assert created.status_code == 201
        appointment_id = UUID(created.json()["id"])
        async with env.factory.begin() as db:
            row = await db.get(Appointment, appointment_id)
            assert row is not None
            row.status = "CANCELLED"
            from app.repositories.users import utc_now

            row.cancelled_at = await utc_now(db)

        detail = await client.get(f"/api/v1/appointments/{appointment_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "CANCELLED"
        replay = await client.post("/api/v1/appointments", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == created.json()
        assert replay.json()["status"] == "UPCOMING"
        await _assert_created_bundle(env.factory, starts_at=starts_at)
    finally:
        await _close_clients(clients)


async def _seed_existing_appointment(env, *, student_id, staff_id, starts_at, key):
    async with env.factory.begin() as db:
        db.add(
            Appointment(
                student_id=student_id,
                staff_id=staff_id,
                purpose_id=env.purpose_id,
                duration_minutes=30,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=30),
                status="UPCOMING",
                creation_request_key=key,
                creation_request_hash=bytes(32),
            )
        )
