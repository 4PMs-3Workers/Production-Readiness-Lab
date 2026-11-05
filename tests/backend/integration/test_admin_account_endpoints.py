import asyncio
import hashlib
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.models import Appointment, AuditEvent, Session, StaffPurpose, User

ORIGIN = {"Origin": "http://localhost:3000"}
ADMIN_ACCOUNTS = "/api/v1/admin/accounts"


async def sign_in(client):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers=ORIGIN,
    )


async def promote_to_admin(factory, user_id):
    async with factory.begin() as db:
        user = await db.get(User, user_id)
        assert user is not None
        user.role = "ADMIN"


def write_headers(client):
    return {**ORIGIN, "X-CSRF-Token": client.cookies["prl_csrf"]}


async def active_account(factory, user_id):
    async with factory() as db:
        user = await db.get(User, user_id)
        assert user is not None
        return user.updated_at.isoformat()


@pytest.mark.mysql
async def test_account_api_rechecks_admin_role_and_requires_csrf(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)

    denied = await client.get(ADMIN_ACCOUNTS)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN_ROLE"

    await promote_to_admin(factory, user_id)
    assert (await client.get(ADMIN_ACCOUNTS)).status_code == 200

    missing_csrf = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "new.staff@example.invalid",
            "name": "New Staff",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=ORIGIN,
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "INVALID_CSRF"


@pytest.mark.mysql
async def test_admin_can_filter_and_paginate_accounts_without_secrets(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    async with factory.begin() as db:
        db.add_all(
            [
                User(
                    id=uuid4(),
                    email="alpha-staff@example.invalid",
                    name="Alpha Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                ),
                User(
                    id=uuid4(),
                    email="beta-staff@example.invalid",
                    name="Beta Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                ),
                User(
                    id=uuid4(),
                    email="inactive-staff@example.invalid",
                    name="Inactive Staff",
                    role="STAFF",
                    password_hash="never-return-this-hash",
                    is_active=False,
                ),
            ]
        )

    response = await client.get(
        f"{ADMIN_ACCOUNTS}?role=STAFF&is_active=true&search=staff&page=1&page_size=1"
    )

    assert response.status_code == 200
    page = response.json()
    assert page["page"] == 1
    assert page["page_size"] == 1
    assert page["total"] == 2
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["role"] == "STAFF" and item["is_active"] is True
    assert "password" not in item and "password_hash" not in item
    assert "never-return-this-hash" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.mysql
async def test_admin_account_creation_normalizes_email_and_rejects_collation_duplicates(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    payload = {
        "email": "  New.Staff@Example.Invalid ",
        "name": "New Staff",
        "role": "STAFF",
        "password": "Initial-password!",
    }

    created = await client.post(ADMIN_ACCOUNTS, json=payload, headers=write_headers(client))

    assert created.status_code == 201
    item = created.json()
    assert item["email"] == "new.staff@example.invalid"
    assert item["role"] == "STAFF"
    assert item["is_active"] is True
    assert "password" not in item and "password_hash" not in item
    assert "Initial-password!" not in created.text
    assert "private" in created.headers["cache-control"]

    duplicate = await client.post(
        ADMIN_ACCOUNTS,
        json={**payload, "email": "NEW.STAFF@example.invalid"},
        headers=write_headers(client),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    async with factory() as db:
        audit_count = await db.scalar(select(func.count()).select_from(AuditEvent))
        audit = await db.scalar(select(AuditEvent))
    assert audit_count == 1
    assert audit is not None
    assert audit.action == "ACCOUNT_CREATED"
    assert "password" not in str(audit.event_metadata).lower()


@pytest.mark.mysql
async def test_account_updates_check_version_and_skip_audit_for_noop(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    version = await active_account(factory, user_id)
    async with factory() as db:
        user = await db.get(User, user_id)
        assert user is not None
        current_name = user.name

    unchanged = await client.patch(
        f"{ADMIN_ACCOUNTS}/{user_id}",
        json={"expected_updated_at": version, "name": current_name},
        headers=write_headers(client),
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["updated_at"] == version

    changed = await client.patch(
        f"{ADMIN_ACCOUNTS}/{user_id}",
        json={"expected_updated_at": version, "name": "Updated administrator"},
        headers=write_headers(client),
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Updated administrator"
    assert changed.json()["updated_at"] != version

    stale = await client.patch(
        f"{ADMIN_ACCOUNTS}/{user_id}",
        json={"expected_updated_at": version, "name": "Overwritten name"},
        headers=write_headers(client),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ACCOUNT_VERSION_CONFLICT"
    async with factory() as db:
        events = await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == str(user_id))
        )
    assert events == 1


@pytest.mark.mysql
async def test_password_reset_revokes_sessions_and_audits_without_secret(auth_env):
    admin_client, factory, admin_id, app = auth_env
    await sign_in(admin_client)
    await promote_to_admin(factory, admin_id)
    created = await admin_client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "reset-target@example.invalid",
            "name": "Reset Target",
            "role": "STAFF",
            "password": "Old-password!",
        },
        headers=write_headers(admin_client),
    )
    assert created.status_code == 201
    target_id = created.json()["id"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as target_client:
        target_login = await target_client.post(
            "/api/v1/auth/login",
            json={"email": "reset-target@example.invalid", "password": "Old-password!"},
            headers=ORIGIN,
        )
        assert target_login.status_code == 200
        assert (await target_client.get("/api/v1/auth/me")).status_code == 200

        reset = await admin_client.post(
            f"{ADMIN_ACCOUNTS}/{target_id}/password",
            json={"expected_updated_at": created.json()["updated_at"], "password": "New-password!"},
            headers=write_headers(admin_client),
        )
        assert reset.status_code == 200
        assert "New-password!" not in reset.text
        assert "password_hash" not in reset.text
        assert (await target_client.get("/api/v1/auth/me")).status_code == 401

    async with factory() as db:
        sessions = list(
            (await db.scalars(select(Session).where(Session.user_id == target_id))).all()
        )
        audit = await db.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == target_id, AuditEvent.action == "ACCOUNT_PASSWORD_RESET"
            )
        )
    assert sessions and all(session.revoked_at is not None for session in sessions)
    assert audit is not None
    assert "password" not in str(audit.event_metadata).lower()


@pytest.mark.mysql
async def test_last_active_admin_race_cannot_remove_both_admins(auth_env):
    first_client, factory, first_id, app = auth_env
    await sign_in(first_client)
    await promote_to_admin(factory, first_id)
    second = await first_client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "second-admin@example.invalid",
            "name": "Second Admin",
            "role": "ADMIN",
            "password": "Second-password!",
        },
        headers=write_headers(first_client),
    )
    assert second.status_code == 201
    second_id = second.json()["id"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as second_client:
        login = await second_client.post(
            "/api/v1/auth/login",
            json={"email": "second-admin@example.invalid", "password": "Second-password!"},
            headers=ORIGIN,
        )
        assert login.status_code == 200
        first_version = await active_account(factory, first_id)
        second_version = second.json()["updated_at"]
        outcomes = await asyncio.gather(
            first_client.patch(
                f"{ADMIN_ACCOUNTS}/{second_id}",
                json={"expected_updated_at": second_version, "is_active": False},
                headers=write_headers(first_client),
            ),
            second_client.patch(
                f"{ADMIN_ACCOUNTS}/{first_id}",
                json={"expected_updated_at": first_version, "is_active": False},
                headers=write_headers(second_client),
            ),
        )

    assert sorted(response.status_code for response in outcomes) == [200, 403]
    async with factory() as db:
        remaining = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "ADMIN", User.is_active.is_(True))
        )
    assert remaining == 1


@pytest.mark.mysql
async def test_last_active_staff_account_cannot_be_deactivated(auth_env):
    client, factory, admin_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, admin_id)
    staff = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "last-staff@example.invalid",
            "name": "Last Staff",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=write_headers(client),
    )
    assert staff.status_code == 201

    response = await client.patch(
        f"{ADMIN_ACCOUNTS}/{staff.json()['id']}",
        json={"expected_updated_at": staff.json()["updated_at"], "is_active": False},
        headers=write_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_ACTIVE_STAFF"


@pytest.mark.mysql
async def test_role_change_preserves_staff_history_and_deactivation_keeps_bookings(
    availability_env,
):
    env = availability_env
    await sign_in(env.client)
    await promote_to_admin(env.factory, env.student_id)
    staff_version = await active_account(env.factory, env.staff_id)

    role_change = await env.client.patch(
        f"{ADMIN_ACCOUNTS}/{env.staff_id}",
        json={"expected_updated_at": staff_version, "role": "STUDENT"},
        headers=write_headers(env.client),
    )
    assert role_change.status_code == 409
    assert role_change.json()["error"]["code"] == "ROLE_CHANGE_REFERENCED"
    async with env.factory() as db:
        assignment = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        staff = await db.get(User, env.staff_id)
    assert assignment is not None and staff is not None and staff.role == "STAFF"


@pytest.mark.mysql
async def test_staff_with_occupying_appointment_cannot_be_deactivated(booking_env):
    env, _, start = booking_env
    async with env.factory.begin() as db:
        db.add(
            Appointment(
                id=uuid4(),
                student_id=env.student_id,
                staff_id=env.staff_id,
                purpose_id=env.purpose_id,
                duration_minutes=30,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                status="UPCOMING",
                creation_request_key=f"fixture-{uuid4().hex}",
                creation_request_hash=hashlib.sha256(uuid4().bytes).digest(),
            )
        )
    await sign_in(env.client)
    await promote_to_admin(env.factory, env.student_id)
    version = await active_account(env.factory, env.staff_id)

    response = await env.client.patch(
        f"{ADMIN_ACCOUNTS}/{env.staff_id}",
        json={"expected_updated_at": version, "is_active": False},
        headers=write_headers(env.client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAFF_HAS_OCCUPYING_APPOINTMENTS"
    async with env.factory() as db:
        staff = await db.get(User, env.staff_id)
        appointment = await db.scalar(
            select(Appointment).where(Appointment.staff_id == env.staff_id)
        )
    assert staff is not None and staff.is_active is True
    assert appointment is not None and appointment.status == "UPCOMING"


@pytest.mark.mysql
async def test_inactive_admin_session_cannot_read_or_mutate_accounts(auth_env):
    client, factory, user_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, user_id)
    async with factory.begin() as db:
        user = await db.get(User, user_id)
        assert user is not None
        user.is_active = False

    read = await client.get(ADMIN_ACCOUNTS)
    write = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "blocked@example.invalid",
            "name": "Blocked",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=write_headers(client),
    )

    assert read.status_code == 401
    assert read.json()["error"]["code"] == "INVALID_SESSION"
    assert write.status_code == 401
    assert write.json()["error"]["code"] == "INVALID_SESSION"


@pytest.mark.mysql
async def test_concurrent_stale_updates_only_commit_once(auth_env):
    client, factory, admin_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, admin_id)
    created = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "concurrent-target@example.invalid",
            "name": "Concurrent Target",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=write_headers(client),
    )
    assert created.status_code == 201
    target_id = created.json()["id"]
    version = created.json()["updated_at"]

    outcomes = await asyncio.gather(
        client.patch(
            f"{ADMIN_ACCOUNTS}/{target_id}",
            json={"expected_updated_at": version, "name": "First Update"},
            headers=write_headers(client),
        ),
        client.patch(
            f"{ADMIN_ACCOUNTS}/{target_id}",
            json={"expected_updated_at": version, "name": "Second Update"},
            headers=write_headers(client),
        ),
    )

    assert sorted(response.status_code for response in outcomes) == [200, 409]
    assert "ACCOUNT_VERSION_CONFLICT" in [
        response.json().get("error", {}).get("code") for response in outcomes
    ]
    async with factory() as db:
        event_count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == target_id, AuditEvent.action == "ACCOUNT_UPDATED")
        )
    assert event_count == 1


@pytest.mark.mysql
async def test_duplicate_email_update_rolls_back_account_and_audit(auth_env):
    client, factory, admin_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, admin_id)
    created = await client.post(
        ADMIN_ACCOUNTS,
        json={
            "email": "duplicate-target@example.invalid",
            "name": "Duplicate Target",
            "role": "STAFF",
            "password": "Initial-password!",
        },
        headers=write_headers(client),
    )
    assert created.status_code == 201
    target_id = created.json()["id"]
    version = created.json()["updated_at"]

    response = await client.patch(
        f"{ADMIN_ACCOUNTS}/{target_id}",
        json={"expected_updated_at": version, "email": "student@example.invalid"},
        headers=write_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    async with factory() as db:
        user = await db.get(User, target_id)
        event_count = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == target_id, AuditEvent.action == "ACCOUNT_UPDATED")
        )
    assert user is not None
    assert user.email == "duplicate-target@example.invalid"
    assert user.updated_at.isoformat() == version
    assert event_count == 0


@pytest.mark.mysql
async def test_unknown_commit_retry_uses_old_version_as_conflict(auth_env, monkeypatch):
    from app.core.transactions import CommitOutcomeUnknown
    from app.services import admin_accounts as account_service

    client, factory, admin_id, _ = auth_env
    await sign_in(client)
    await promote_to_admin(factory, admin_id)
    version = await active_account(factory, admin_id)

    async def commit_then_lose_response(_db, transaction, **_kwargs):
        await transaction.commit()
        raise CommitOutcomeUnknown()

    monkeypatch.setattr(account_service, "commit_safely", commit_then_lose_response)
    payload = {"expected_updated_at": version, "name": "Committed, response lost"}
    uncertain = await client.patch(
        f"{ADMIN_ACCOUNTS}/{admin_id}", json=payload, headers=write_headers(client)
    )
    retry = await client.patch(
        f"{ADMIN_ACCOUNTS}/{admin_id}", json=payload, headers=write_headers(client)
    )

    assert uncertain.status_code == 503
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "ACCOUNT_VERSION_CONFLICT"
    async with factory() as db:
        user = await db.get(User, admin_id)
        events = await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == str(admin_id), AuditEvent.action == "ACCOUNT_UPDATED")
        )
    assert user is not None and user.name == "Committed, response lost"
    assert events == 1


@pytest.mark.mysql
async def test_booking_and_staff_deactivation_serialize_on_settings_lock(booking_env):
    env, _, starts_at = booking_env
    await sign_in(env.client)
    await promote_to_admin(env.factory, env.student_id)
    async with env.factory.begin() as db:
        original = await db.get(User, env.student_id)
        assert original is not None
        booking_student = User(
            email="racing-booking-student@example.invalid",
            name="Booking Student",
            role="STUDENT",
            password_hash=original.password_hash,
        )
        db.add(booking_student)
        await db.flush()
        booking_student_id = booking_student.id
        staff = await db.get(User, env.staff_id)
        assert staff is not None
        staff_version = staff.updated_at.isoformat()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://localhost:3000"
    ) as student_client:
        login = await student_client.post(
            "/api/v1/auth/login",
            json={
                "email": "racing-booking-student@example.invalid",
                "password": "  Good-password!  ",
            },
            headers=ORIGIN,
        )
        assert login.status_code == 200
        booking, deactivation = await asyncio.gather(
            student_client.post(
                "/api/v1/appointments",
                json={
                    "purpose_id": str(env.purpose_id),
                    "staff_id": str(env.staff_id),
                    "starts_at": starts_at.isoformat(),
                },
                headers={**write_headers(student_client), "Idempotency-Key": uuid4().hex},
            ),
            env.client.patch(
                f"{ADMIN_ACCOUNTS}/{env.staff_id}",
                json={"expected_updated_at": staff_version, "is_active": False},
                headers=write_headers(env.client),
            ),
        )

    if booking.status_code == 201:
        assert deactivation.status_code == 409
        assert deactivation.json()["error"]["code"] == "STAFF_HAS_OCCUPYING_APPOINTMENTS"
        async with env.factory() as db:
            staff = await db.get(User, env.staff_id)
            appointment = await db.scalar(
                select(Appointment).where(
                    Appointment.student_id == booking_student_id,
                    Appointment.staff_id == env.staff_id,
                )
            )
        assert staff is not None and staff.is_active
        assert appointment is not None and appointment.status == "UPCOMING"
    else:
        assert booking.status_code == 404
        assert booking.json()["error"]["code"] == "NOT_FOUND"
        assert deactivation.status_code == 200
        async with env.factory() as db:
            staff = await db.get(User, env.staff_id)
            appointment = await db.scalar(
                select(Appointment).where(
                    Appointment.student_id == booking_student_id,
                    Appointment.staff_id == env.staff_id,
                )
            )
        assert staff is not None and not staff.is_active
        assert appointment is None
