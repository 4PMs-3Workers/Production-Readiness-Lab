"""Cancellation races use real connections and observed InnoDB lock barriers."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from tests.backend.integration.test_booking_service import _create, _payload, _seed_appointment
from tests.backend.integration.test_student_cancellation import cancel, seed

from app.api.errors import AppError
from app.models import Appointment, AuditEvent, Notification
from app.repositories import users
from app.schemas.availability import AvailabilityQuery
from app.services.availability import get_availability


async def wait_for_lock(observer, connection_id):
    """Wait for server evidence, never infer contention from elapsed time."""
    async with asyncio.timeout(3):
        while not await observer.scalar(
            text(
                "SELECT COUNT(*) FROM performance_schema.data_lock_waits w "
                "JOIN performance_schema.threads t ON t.THREAD_ID=w.REQUESTING_THREAD_ID "
                "WHERE t.PROCESSLIST_ID=:id"
            ),
            {"id": connection_id},
        ):
            await asyncio.sleep(0)


async def ordered_race(env, monkeypatch, shared_id, first, second, before_release=None):
    """Three independent sessions: lock holder, contender, server observer."""
    acquired, release, entering = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = users.lock_by_id
    ids = {}
    async with env.factory() as holder, env.factory() as contender, env.factory() as observer:
        observer_id = await observer.scalar(text("SELECT CONNECTION_ID()"))

        async def lock(db, account_id):
            if account_id != shared_id:
                return await original(db, account_id)
            ids[id(db)] = await db.scalar(text("SELECT CONNECTION_ID()"))
            if db is contender:
                entering.set()
            row = await original(db, account_id)
            if db is holder:
                acquired.set()
                await release.wait()
            return row

        monkeypatch.setattr(users, "lock_by_id", lock)

        async def outcome(call, db):
            try:
                return await call(db)
            except AppError as error:
                return error

        tasks = [asyncio.create_task(outcome(first, holder))]
        try:
            async with asyncio.timeout(3):
                await acquired.wait()
            tasks.append(asyncio.create_task(outcome(second, contender)))
            async with asyncio.timeout(3):
                await entering.wait()
            await wait_for_lock(observer, ids[id(contender)])
            assert len({observer_id, *ids.values()}) == 3
            if before_release is not None:
                before_release()
            release.set()
            async with asyncio.timeout(8):
                return await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def assert_cancel_bundle(env, appointment_id, reason):
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert row.status == "CANCELLED"
        assert row.cancel_reason == reason
        assert row.cancelled_by == env.student_id
        audits = list(
            (
                await db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "APPOINTMENT_CANCELLED",
                        AuditEvent.entity_id == str(appointment_id),
                    )
                )
            ).all()
        )
        notices = list(
            (
                await db.scalars(
                    select(Notification).where(
                        Notification.event_type == "APPOINTMENT_CANCELLED",
                        Notification.appointment_id == appointment_id,
                    )
                )
            ).all()
        )
        assert len(audits) == 1
        assert len(notices) == 2
        assert {item.recipient_id for item in notices} == {env.student_id, env.staff_id}
        return row.cancelled_at


async def test_two_cancellations_serialize_one_transition_and_preserve_first_reason(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    first, second = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        lambda db: cancel(env, appointment_id, auth_settings, db=db, reason="first"),
        lambda db: cancel(env, appointment_id, auth_settings, db=db, reason="second"),
    )
    assert not isinstance(first, AppError)
    assert not isinstance(second, AppError)
    assert first == second
    assert first.cancelled_at == await assert_cancel_bundle(env, appointment_id, "first")
    replay = await cancel(env, appointment_id, auth_settings, reason="third")
    assert replay == first
    await assert_cancel_bundle(env, appointment_id, "first")


@pytest.mark.parametrize("resource", ["staff-slot", "student-quota"])
@pytest.mark.parametrize("cancellation_first", [True, False])
async def test_booking_and_cancellation_follow_lock_commit_order(
    booking_env, auth_settings, monkeypatch, resource, cancellation_first
):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    quota = resource == "student-quota"
    if quota:
        for offset in (2, 3):
            await _seed_appointment(
                env,
                student_id=env.student_id,
                staff_id=env.other_staff_id,
                purpose_id=env.purpose_id,
                starts_at=start + timedelta(days=offset),
            )

    async def cancellation(db):
        return await cancel(env, appointment_id, auth_settings, db=db, reason="release")

    async def booking(db):
        payload = _payload(env, start + timedelta(minutes=30) if quota else start)
        if quota:
            # Different staff ensures only the student lock can serialize the quota race.
            payload = payload.model_copy(update={"staff_id": env.other_staff_id})
        return await _create(
            env,
            payload,
            auth_settings,
            student_id=env.student_id if quota else env.other_student_id,
            db=db,
        )

    calls = (cancellation, booking) if cancellation_first else (booking, cancellation)
    shared_id = env.student_id if quota else env.staff_id
    results = await ordered_race(env, monkeypatch, shared_id, *calls)
    booking_result = results[1 if cancellation_first else 0]
    if cancellation_first:
        assert not isinstance(booking_result, AppError)
        assert booking_result[1] is False
    else:
        assert isinstance(booking_result, AppError)
        assert booking_result.code == ("BOOKING_QUOTA_EXCEEDED" if quota else "BOOKING_CONFLICT")
    await assert_cancel_bundle(env, appointment_id, "release")
    async with env.factory() as db:
        active = list(
            (
                await db.scalars(
                    select(Appointment).where(
                        Appointment.status.in_(["UPCOMING", "CHANGE_PENDING"])
                    )
                )
            ).all()
        )
        assert len(active) == (2 if quota else 0) + int(cancellation_first)
        assert len([row for row in active if row.student_id == env.student_id]) <= 3
        for index, row in enumerate(active):
            for other in active[index + 1 :]:
                if row.student_id == other.student_id or row.staff_id == other.staff_id:
                    assert row.ends_at <= other.starts_at or other.ends_at <= row.starts_at


async def test_cancel_releases_availability_slot_quota_and_keeps_booking_receipt(
    booking_env, auth_settings
):
    env, date, start = booking_env
    payload = _payload(env, start)
    original, _ = await _create(env, payload, auth_settings, key="released-receipt")
    for offset in (2, 3):
        await _seed_appointment(
            env,
            student_id=env.student_id,
            staff_id=env.other_staff_id,
            purpose_id=env.purpose_id,
            starts_at=start + timedelta(days=offset),
        )

    async def slots():
        async with env.factory() as db:
            response = await get_availability(
                db,
                AvailabilityQuery(purpose_id=env.purpose_id, staff_id=env.staff_id, date=date),
                student_id=env.student_id,
                settings=auth_settings,
                clock=lambda: datetime.now(UTC),
            )
            return [slot.starts_at.root for slot in response.slots]

    assert start not in await slots()
    with pytest.raises(AppError) as error:
        await _create(env, _payload(env, start + timedelta(minutes=30)), auth_settings)
    assert error.value.code == "BOOKING_QUOTA_EXCEEDED"
    await cancel(env, original.id, auth_settings)
    assert start in await slots()
    replacement, replayed = await _create(env, payload, auth_settings, key="replacement")
    assert not replayed and replacement.id != original.id
    replay, replayed = await _create(env, payload, auth_settings, key="released-receipt")
    assert replayed and replay == original and replay.status == "UPCOMING"
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Appointment)) == 4
        assert (await db.get(Appointment, original.id)).status == "CANCELLED"
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 3
        assert await db.scalar(select(func.count()).select_from(Notification)) == 6
