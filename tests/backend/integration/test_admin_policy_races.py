"""Real lock hand-offs between administrative writes and booking operations."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.backend.integration.test_admin_booking_configuration import (
    CONFIGURATION,
    PURPOSES,
    policy_body,
    purpose_body,
)
from tests.backend.integration.test_booking_concurrency import _booking_headers, _clients, _payload

from app.models import Appointment, StaffPurpose, User
from app.repositories import appointments

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
