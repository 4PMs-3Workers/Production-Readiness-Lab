"""Real lock hand-offs between administrative writes and booking operations."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.backend.integration.test_admin_booking_configuration import (
    CONFIGURATION,
    PURPOSES,
    policy_body,
    purpose_body,
    window,
)
from tests.backend.integration.test_booking_concurrency import _booking_headers, _clients, _payload
from tests.backend.integration.test_change_proposals import create, payload
from tests.backend.integration.test_student_cancellation import seed

from app.dependencies.clock import get_clock
from app.models import Appointment, AuditEvent, ChangeProposal, StaffPurpose, User
from app.repositories import appointments, availability

pytestmark = [pytest.mark.mysql, pytest.mark.booking_stress]


async def admin_id(env):
    identifier = uuid4()
    async with env.factory.begin() as db:
        db.add(
            User(
                id=identifier,
                name="Policy Admin",
                email="policy-admin@example.invalid",
                role="ADMIN",
                password_hash="unused-test-hash",
            )
        )
    return identifier


async def commit_during_read(read, write, read_started, resume_read):
    async with asyncio.TaskGroup() as tasks:
        pending = tasks.create_task(read())
        try:
            await asyncio.wait_for(read_started.wait(), 5)
            mutation = await write()
            assert mutation.status_code == 200, mutation.json()
        finally:
            resume_read.set()
    result = pending.result()
    assert result.status_code == 200, result.json()
    return result.json()


async def test_availability_uses_one_snapshot_for_settings_and_windows(booking_env, monkeypatch):
    env, day, starts_at = booking_env
    (admin, csrf), (student, _) = await _clients(booking_env, [await admin_id(env), env.student_id])
    read_started, resume_read = asyncio.Event(), asyncio.Event()
    original = availability.read_settings

    async def pause_after_settings(db):
        row = await original(db)
        read_started.set()
        await resume_read.wait()
        return row

    params = {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(env.staff_id),
        "date": day.isoformat(),
    }
    async with admin, student:
        before = (await admin.get(CONFIGURATION)).json()
        replacement = policy_body(
            before["updated_at"],
            start_step_minutes=30,
            windows=[window(day.isoweekday(), "12:00:00", "13:00:00")],
        )
        with monkeypatch.context() as patch:
            patch.setattr(availability, "read_settings", pause_after_settings)
            old = await commit_during_read(
                lambda: student.get("/api/v1/availability", params=params),
                lambda: admin.put(
                    CONFIGURATION, json=replacement, headers=_booking_headers(csrf, "replace")
                ),
                read_started,
                resume_read,
            )
        new = await student.get("/api/v1/availability", params=params)
        assert new.status_code == 200, new.json()

    def starts(*minutes):
        return [
            (starts_at + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
            for offset in minutes
        ]

    assert [slot["starts_at"] for slot in old["slots"]] == starts(*range(0, 91, 15))
    assert [slot["starts_at"] for slot in new.json()["slots"]] == starts(240, 270)


async def test_student_purpose_count_and_page_use_one_snapshot(booking_env, monkeypatch):
    env, _, _ = booking_env
    (admin, csrf), (student, _) = await _clients(booking_env, [await admin_id(env), env.student_id])
    read_started, resume_read = asyncio.Event(), asyncio.Event()
    original = AsyncSession.scalar

    async def pause_after_purpose_count(db, statement, *args, **kwargs):
        result = await original(db, statement, *args, **kwargs)
        sql = str(statement).lower()
        if "count(" in sql and "from purposes" in sql:
            read_started.set()
            await resume_read.wait()
        return result

    path = f"{PURPOSES}/{env.purpose_id}"
    async with admin, student:
        before = (await admin.get(path)).json()
        listing = await student.get("/api/v1/purposes")
        assert listing.status_code == 200, listing.json()
        baseline = listing.json()
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "scalar", pause_after_purpose_count)
            old = await commit_during_read(
                lambda: student.get("/api/v1/purposes"),
                lambda: admin.put(
                    path,
                    json=purpose_body(before, is_active=False),
                    headers=_booking_headers(csrf, "deactivate"),
                ),
                read_started,
                resume_read,
            )
        new = await student.get("/api/v1/purposes")
        assert new.status_code == 200, new.json()
    assert old == baseline
    remaining = [item for item in old["items"] if item["id"] != str(env.purpose_id)]
    assert new.json() == {**old, "items": remaining, "total": old["total"] - 1}


@pytest.mark.parametrize(
    ("day", "opens", "closes", "utc_hour"),
    [
        (date(2026, 3, 8), "02:00:00", "04:00:00", 12),
        (date(2026, 11, 1), "00:00:00", "01:00:00", 13),
    ],
)
async def test_admin_dst_boundary_preserves_unaffected_availability(
    booking_env, day, opens, closes, utc_hour
):
    env, _, _ = booking_env
    (admin, csrf), (student, _) = await _clients(booking_env, [await admin_id(env), env.student_id])
    now = datetime.combine(day - timedelta(days=1), time(12), UTC)
    env.app.dependency_overrides[get_clock] = lambda: lambda: now
    async with admin, student:
        policy = (await admin.get(CONFIGURATION)).json()
        response = await admin.put(
            CONFIGURATION,
            json=policy_body(
                policy["updated_at"],
                timezone="America/New_York",
                start_step_minutes=30,
                windows=[
                    window(day.isoweekday(), opens, closes),
                    window(day.isoweekday(), "08:00:00", "09:00:00"),
                ],
            ),
            headers=_booking_headers(csrf, "dst-policy"),
        )
        assert response.status_code == 200, response.json()
        response = await student.get(
            "/api/v1/availability",
            params={
                "purpose_id": str(env.purpose_id),
                "staff_id": str(env.staff_id),
                "date": str(day),
            },
        )
        assert response.status_code == 200, response.json()
        assert [slot["starts_at"] for slot in response.json()["slots"]] == [
            f"{day}T{utc_hour}:00:00Z",
            f"{day}T{utc_hour}:30:00Z",
        ]


async def commit_admin_first(monkeypatch, write, dependent):
    """Hold the real exclusive lock until the dependent requests its shared lock."""
    exclusive_ready, shared_requested, proceed = (asyncio.Event() for _ in range(3))
    exclusive = appointments.lock_service_settings_for_account_change
    shared = appointments.lock_service_settings

    async def pause_writer(db):
        result = await exclusive(db)
        assert result is not None
        exclusive_ready.set()
        await proceed.wait()
        return result

    async def signal_reader(db):
        shared_requested.set()
        return await shared(db)

    with monkeypatch.context() as patch:
        patch.setattr(appointments, "lock_service_settings_for_account_change", pause_writer)
        patch.setattr(appointments, "lock_service_settings", signal_reader)
        async with asyncio.TaskGroup() as tasks:
            writer = tasks.create_task(write())
            try:
                await asyncio.wait_for(exclusive_ready.wait(), 5)
                reader = tasks.create_task(dependent())
                await asyncio.wait_for(shared_requested.wait(), 5)
                assert not reader.done()
            finally:
                proceed.set()
    return writer.result(), reader.result()


@pytest.mark.parametrize(
    "change,status,error",
    [
        ("closed", 409, "BOOKING_CONFLICT"),
        ("deactivated", 404, "NOT_FOUND"),
        ("longer", 409, "BOOKING_CONFLICT"),
    ],
)
async def test_admin_changes_win_before_booking_without_rewriting_confirmed_slots(
    booking_env, monkeypatch, change, status, error
):
    env, selected_day, start = booking_env
    clients = await _clients(
        booking_env, [await admin_id(env), env.student_id, env.other_student_id]
    )
    (admin, admin_csrf), (student, student_csrf), (other, other_csrf) = clients
    booking = "/api/v1/appointments"
    async with admin, student, other:
        existing = await student.post(
            booking, json=_payload(env, start), headers=_booking_headers(student_csrf, "existing")
        )
        assert existing.status_code == 201, existing.json()
        identifier = UUID(existing.json()["id"])
        late = start + timedelta(minutes=90)
        if change == "closed":
            before = (await admin.get(CONFIGURATION)).json()
            path, body = CONFIGURATION, policy_body(before["updated_at"])
        else:
            path = f"{PURPOSES}/{env.purpose_id}"
            item = (await admin.get(path)).json()
            body = (
                purpose_body(item, is_active=False)
                if change == "deactivated"
                else purpose_body(item, duration_minutes=45)
            )

        mutation, candidate = await commit_admin_first(
            monkeypatch,
            lambda: admin.put(path, json=body, headers=_booking_headers(admin_csrf, "policy")),
            lambda: other.post(
                booking, json=_payload(env, late), headers=_booking_headers(other_csrf, "later")
            ),
        )
        assert mutation.status_code == 200, mutation.json()
        assert candidate.status_code == status, candidate.json()
        assert candidate.json()["error"]["code"] == error

        if change == "closed":
            preview = await other.get(
                "/api/v1/availability",
                params={
                    "purpose_id": str(env.purpose_id),
                    "staff_id": str(env.staff_id),
                    "date": selected_day.isoformat(),
                },
            )
            assert preview.status_code == 200 and preview.json()["slots"] == []

    async with env.factory() as db:
        appointments_found = list(await db.scalars(select(Appointment)))
        assert len(appointments_found) == 1
        row = appointments_found[0]
        assert row.id == identifier and row.status == "UPCOMING"
        assert (row.starts_at, row.ends_at, row.duration_minutes) == (
            start,
            start + timedelta(minutes=30),
            30,
        )
        link = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert link is not None and link.is_accepting


@pytest.mark.parametrize(
    "change,status,error", [("closed", 409, "STAFF_UNAVAILABLE"), ("inactive", 404, "NOT_FOUND")]
)
async def test_waiting_proposal_acceptance_revalidates_after_admin_changes(
    booking_env, monkeypatch, change, status, error
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    proposal, _ = await create(
        env, identifier, payload(start + timedelta(hours=1)), env.app.state.settings
    )
    (admin, admin_csrf), (student, student_csrf) = await _clients(
        booking_env, [await admin_id(env), env.student_id]
    )
    async with admin, student:
        if change == "closed":
            before = (await admin.get(CONFIGURATION)).json()
            path, body = CONFIGURATION, policy_body(before["updated_at"])
        else:
            path = f"{PURPOSES}/{env.purpose_id}"
            before = (await admin.get(path)).json()
            body = purpose_body(before, is_active=False)
        accept = f"/api/v1/appointments/{identifier}/change-proposals/{proposal.id}/accept"
        updated, rejected = await commit_admin_first(
            monkeypatch,
            lambda: admin.put(path, json=body, headers=_booking_headers(admin_csrf, "policy")),
            lambda: student.post(
                accept, json={}, headers=_booking_headers(student_csrf, "proposal")
            ),
        )
        assert updated.status_code == 200, updated.json()
        assert rejected.status_code == status, rejected.json()
        assert rejected.json()["error"]["code"] == error

    async with env.factory() as db:
        stored = await db.get(Appointment, identifier)
        assert stored is not None and stored.status == "CHANGE_PENDING"
        assert (stored.starts_at, stored.ends_at, stored.duration_minutes) == (
            start,
            start + timedelta(minutes=30),
            30,
        )
        pending = await db.get(ChangeProposal, proposal.id)
        assert pending is not None and pending.status == "PENDING"
        assert (
            await db.scalar(
                select(AuditEvent.id).where(AuditEvent.action == "CHANGE_PROPOSAL_ACCEPTED")
            )
            is None
        )


async def test_staff_cannot_enable_a_purpose_deactivated_by_waited_admin_write(
    booking_env, monkeypatch
):
    env, _, _ = booking_env
    (admin, admin_csrf), (staff, staff_csrf) = await _clients(
        booking_env, [await admin_id(env), env.staff_id]
    )
    path = f"{PURPOSES}/{env.purpose_id}"
    preference = f"/api/v1/staff/me/purposes/{env.purpose_id}"
    async with admin, staff:
        before = (await admin.get(path)).json()
        updated, rejected = await commit_admin_first(
            monkeypatch,
            lambda: admin.put(
                path,
                json=purpose_body(before, is_active=False),
                headers=_booking_headers(admin_csrf, "policy"),
            ),
            lambda: staff.put(
                preference,
                json={"is_accepting": True},
                headers=_booking_headers(staff_csrf, "preference"),
            ),
        )
        assert updated.status_code == 200, updated.json()
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "PURPOSE_INACTIVE"
        disabled = await staff.put(
            preference,
            json={"is_accepting": False},
            headers=_booking_headers(staff_csrf, "preference"),
        )
        assert disabled.status_code == 200 and not disabled.json()["is_accepting"]
    async with env.factory() as db:
        link = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert link is not None and not link.is_accepting
