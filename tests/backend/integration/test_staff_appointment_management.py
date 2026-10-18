"""MySQL-backed assigned appointment history and privacy behavior."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from time import monotonic
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction

from app.api.errors import AppError
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
from app.schemas.appointments import AppointmentCancelRequest
from app.schemas.staff_appointments import StaffAppointmentListQuery
from app.services import cancellation, staff_appointments

pytestmark = pytest.mark.mysql


async def _seed(env, *, appointment_id: UUID, staff_id: UUID, starts_at: datetime):
    async with env.factory.begin() as db:
        db.add(
            Appointment(
                id=appointment_id,
                student_id=env.student_id,
                staff_id=staff_id,
                purpose_id=env.purpose_id,
                duration_minutes=45,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=45),
                status="UPCOMING",
                other_reason="Private consultation context",
                location="Room 4",
                creation_request_key=uuid4().hex,
                creation_request_hash=sha256(appointment_id.bytes).digest(),
            )
        )


async def test_staff_reads_assignment_history_timezone_and_private_fields(availability_env):
    env = availability_env
    service_settings = env.app.state.settings
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    local_day = date(2026, 10, 16)
    lower = datetime.combine(local_day, datetime.min.time(), zone).astimezone(UTC)
    upper = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), zone).astimezone(
        UTC
    )
    ids = [UUID(int=value) for value in (100, 101, 102, 103, 104)]
    await _seed(
        env,
        appointment_id=ids[0],
        staff_id=env.staff_id,
        starts_at=lower - timedelta(microseconds=1),
    )
    await _seed(
        env, appointment_id=ids[1], staff_id=env.staff_id, starts_at=lower + timedelta(hours=1)
    )
    await _seed(
        env, appointment_id=ids[2], staff_id=env.staff_id, starts_at=lower + timedelta(hours=1)
    )
    await _seed(
        env, appointment_id=ids[3], staff_id=env.staff_id, starts_at=upper - timedelta(minutes=1)
    )
    await _seed(
        env,
        appointment_id=ids[4],
        staff_id=env.other_staff_id,
        starts_at=lower + timedelta(hours=2),
    )
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        purpose.name = "Historical purpose name"
        purpose.is_active = False
        purpose.duration_minutes = 90
        staff = await db.get(User, env.staff_id)
        staff.name = "Historical assigned staff"
        staff.is_active = False
        assignment = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assignment.is_accepting = False

    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with env.factory() as db:
            page = await staff_appointments.list_staff_appointments(
                db,
                StaffAppointmentListQuery(date=local_day, page=1, page_size=10),
                staff_id=env.staff_id,
                settings=service_settings,
            )
        assert len(statements) == 3  # settings, count, and one bounded page query.
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert page.total == 3
    assert [item.id for item in page.items] == ids[1:4]
    first = page.items[0]
    assert (first.duration_minutes, first.purpose_name) == (45, "Historical purpose name")
    assert first.student_name == "Test Student"
    assert not hasattr(first, "description")
    assert "other_reason" not in first.model_dump()

    async with env.factory() as db:
        detail = await staff_appointments.get_staff_appointment(
            db,
            appointment_id=ids[1],
            staff_id=env.staff_id,
            settings=service_settings,
        )
    assert detail.description == "Private consultation context"
    assert detail.duration_minutes == 45

    async with env.factory() as db:
        with pytest.raises(AppError) as missing:
            await staff_appointments.get_staff_appointment(
                db,
                appointment_id=ids[1],
                staff_id=env.other_staff_id,
                settings=service_settings,
            )
    async with env.factory() as db:
        with pytest.raises(AppError) as absent:
            await staff_appointments.get_staff_appointment(
                db,
                appointment_id=UUID(int=999),
                staff_id=env.staff_id,
                settings=service_settings,
            )
    assert (missing.value.status_code, missing.value.code) == (404, "NOT_FOUND")
    assert (absent.value.status_code, absent.value.code) == (404, "NOT_FOUND")


async def test_staff_list_status_filter_and_empty_page(availability_env):
    env = availability_env
    start = datetime(2026, 10, 16, 1, tzinfo=UTC)
    appointment_id = UUID(int=200)
    await _seed(env, appointment_id=appointment_id, staff_id=env.staff_id, starts_at=start)
    async with env.factory() as db:
        page = await staff_appointments.list_staff_appointments(
            db,
            StaffAppointmentListQuery(status="CANCELLED", page=2, page_size=10),
            staff_id=env.staff_id,
            settings=env.app.state.settings,
        )
    assert (page.total, page.items, page.page, page.page_size) == (0, [], 2, 10)


def _staff_context(staff_id: UUID) -> AuthContext:
    return AuthContext(
        session_id=uuid4(),
        user_id=staff_id,
        email="staff@example.invalid",
        name="Assigned staff",
        role="STAFF",
        expires_at=datetime(2026, 10, 20, tzinfo=UTC),
    )


async def _cancel(env, appointment_id: UUID, db=None):
    if db is None:
        async with env.factory() as session:
            return await _cancel(env, appointment_id, session)
    return await cancellation.cancel_staff_appointment(
        db,
        appointment_id=appointment_id,
        payload=AppointmentCancelRequest(reason="private staff reason"),
        context=_staff_context(env.staff_id),
        request_id="staff-cancel-test",
        settings=env.app.state.settings,
        deadline=asyncio.get_running_loop().time() + 5,
    )


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 10, 17, 16, 59, 59, 999999, tzinfo=UTC), 200),
        (datetime(2026, 10, 17, 17, 0, tzinfo=UTC), 409),
        (datetime(2026, 10, 17, 17, 0, 0, 1, tzinfo=UTC), 409),
    ],
)
async def test_staff_cancel_uses_strict_business_midnight_and_atomic_bundle(
    availability_env, monkeypatch, now, expected
):
    env = availability_env
    appointment_id = UUID(int=300)
    starts_at = datetime(2026, 10, 17, 18, tzinfo=UTC)  # Oct 18 in Asia/Ho_Chi_Minh.
    await _seed(env, appointment_id=appointment_id, staff_id=env.staff_id, starts_at=starts_at)

    async def db_now(db):
        return now

    monkeypatch.setattr(cancellation.users, "utc_now", db_now)
    if expected == 200:
        result = await _cancel(env, appointment_id)
        assert result.status == "CANCELLED"
        assert result.cancelled_at.root == now
        async with env.factory() as db:
            row = await db.get(Appointment, appointment_id)
            assert (row.cancelled_by, row.cancel_reason) == (env.staff_id, "private staff reason")
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "APPOINTMENT_CANCELLED")
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.appointment_id == appointment_id)
                )
                == 2
            )

        # Repeating cancellation returns the original receipt and adds no side effects.
        async def after_cutoff(db):
            return now + timedelta(days=1)

        monkeypatch.setattr(cancellation.users, "utc_now", after_cutoff)
        repeated = await _cancel(env, appointment_id)
        assert repeated.cancelled_at == result.cancelled_at
    else:
        with pytest.raises(AppError) as error:
            await _cancel(env, appointment_id)
        assert (error.value.status_code, error.value.code) == (
            expected,
            "CANCELLATION_DEADLINE_PASSED",
        )
        async with env.factory() as db:
            row = await db.get(Appointment, appointment_id)
            assert row.status == "UPCOMING"
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
            assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_staff_cancel_resolves_pending_proposal(availability_env, monkeypatch):
    env = availability_env
    appointment_id = UUID(int=301)
    starts_at = datetime(2026, 10, 17, 18, tzinfo=UTC)
    await _seed(env, appointment_id=appointment_id, staff_id=env.staff_id, starts_at=starts_at)
    async with env.factory.begin() as db:
        appointment = await db.get(Appointment, appointment_id)
        appointment.status = "CHANGE_PENDING"
        db.add(
            ChangeProposal(
                appointment_id=appointment_id,
                created_by=env.student_id,
                proposed_starts_at=starts_at + timedelta(days=1),
                proposed_ends_at=starts_at + timedelta(days=1, minutes=45),
                proposed_location="Room 5",
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )

    async def db_now(db):
        return datetime(2026, 10, 17, 16, tzinfo=UTC)

    monkeypatch.setattr(cancellation.users, "utc_now", db_now)
    await _cancel(env, appointment_id)
    async with env.factory() as db:
        proposal = await db.scalar(
            select(ChangeProposal).where(ChangeProposal.appointment_id == appointment_id)
        )
        assert (proposal.status, proposal.resolved_by, proposal.resolved_at) == (
            "CANCELLED",
            env.staff_id,
            datetime(2026, 10, 17, 16, tzinfo=UTC),
        )


async def test_staff_cancel_reads_clock_after_waiting_for_proposal_lock(
    availability_env, monkeypatch
):
    env = availability_env
    appointment_id = UUID(int=306)
    starts_at = datetime(2026, 10, 17, 18, tzinfo=UTC)
    await _seed(env, appointment_id=appointment_id, staff_id=env.staff_id, starts_at=starts_at)
    async with env.factory.begin() as db:
        appointment = await db.get(Appointment, appointment_id)
        appointment.status = "CHANGE_PENDING"
        db.add(
            ChangeProposal(
                appointment_id=appointment_id,
                created_by=env.student_id,
                proposed_starts_at=starts_at + timedelta(days=1),
                proposed_ends_at=starts_at + timedelta(days=1, minutes=45),
                proposed_location="Room 5",
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )

    clock_called = asyncio.Event()
    proposal_lock_requested = asyncio.Event()
    clock_value = datetime(2026, 10, 17, 16, 59, tzinfo=UTC)

    async def db_now(db):
        clock_called.set()
        return clock_value

    monkeypatch.setattr(cancellation.users, "utc_now", db_now)
    engine = env.factory.kw["bind"].sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "change_proposals" in statement and "FOR UPDATE" in statement:
            proposal_lock_requested.set()

    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with env.factory() as blocker:
            transaction = await blocker.begin()
            await blocker.scalar(
                select(ChangeProposal)
                .where(ChangeProposal.appointment_id == appointment_id)
                .with_for_update()
            )
            task = asyncio.create_task(_cancel(env, appointment_id))
            await asyncio.wait_for(proposal_lock_requested.wait(), timeout=2)
            await asyncio.sleep(0.05)
            assert not clock_called.is_set()
            clock_value = datetime(2026, 10, 17, 17, tzinfo=UTC)
            await transaction.commit()
            with pytest.raises(AppError) as error:
                await task
            assert error.value.code == "CANCELLATION_DEADLINE_PASSED"
    finally:
        event.remove(engine, "before_cursor_execute", capture)


async def test_staff_cancel_hides_foreign_assignment_and_inactive_actor(availability_env):
    env = availability_env
    appointment_id = UUID(int=302)
    await _seed(
        env,
        appointment_id=appointment_id,
        staff_id=env.staff_id,
        starts_at=datetime(2026, 10, 17, 18, tzinfo=UTC),
    )
    async with env.factory() as db:
        with pytest.raises(AppError) as error:
            await cancellation.cancel_staff_appointment(
                db,
                appointment_id=appointment_id,
                payload=AppointmentCancelRequest(),
                context=_staff_context(env.other_staff_id),
                request_id="staff-cancel-test",
                settings=env.app.state.settings,
                deadline=monotonic() + 5,
            )
    assert (error.value.status_code, error.value.code) == (404, "NOT_FOUND")
    async with env.factory.begin() as db:
        staff = await db.get(User, env.staff_id)
        staff.is_active = False
    async with env.factory() as db:
        with pytest.raises(AppError) as inactive:
            await cancellation.cancel_staff_appointment(
                db,
                appointment_id=appointment_id,
                payload=AppointmentCancelRequest(),
                context=_staff_context(env.staff_id),
                request_id="staff-cancel-test",
                settings=env.app.state.settings,
                deadline=monotonic() + 5,
            )
    assert (inactive.value.status_code, inactive.value.code) == (404, "NOT_FOUND")


async def test_student_and_staff_cancel_race_commits_one_bundle(availability_env, monkeypatch):
    from tests.backend.integration.test_booking_service import _context

    env = availability_env
    appointment_id = UUID(int=303)
    starts_at = datetime(2026, 10, 17, 18, tzinfo=UTC)
    await _seed(env, appointment_id=appointment_id, staff_id=env.staff_id, starts_at=starts_at)

    async def before_cutoff(db):
        return datetime(2026, 10, 17, 16, tzinfo=UTC)

    monkeypatch.setattr(cancellation.users, "utc_now", before_cutoff)
    barrier = asyncio.Barrier(2)

    async def staff_cancel():
        await barrier.wait()
        return await _cancel(env, appointment_id)

    async def student_cancel():
        await barrier.wait()
        async with env.factory() as db:
            return await cancellation.cancel_appointment(
                db,
                appointment_id=appointment_id,
                payload=AppointmentCancelRequest(reason="student request"),
                context=_context(env.student_id),
                request_id="student-cancel-test",
                settings=env.app.state.settings,
                deadline=asyncio.get_running_loop().time() + 5,
            )

    results = await asyncio.gather(staff_cancel(), student_cancel())
    assert [result.status for result in results] == ["CANCELLED", "CANCELLED"]
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert row.status == "CANCELLED"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "APPOINTMENT_CANCELLED")
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.appointment_id == appointment_id)
            )
            == 2
        )


async def test_staff_cancel_precommit_failure_rolls_back(availability_env, monkeypatch):
    env = availability_env
    appointment_id = UUID(int=304)
    await _seed(
        env,
        appointment_id=appointment_id,
        staff_id=env.staff_id,
        starts_at=datetime(2026, 10, 17, 18, tzinfo=UTC),
    )

    async def before_cutoff(db):
        return datetime(2026, 10, 17, 16, tzinfo=UTC)

    monkeypatch.setattr(cancellation.users, "utc_now", before_cutoff)
    async with env.factory() as db:
        original_flush = db.flush

        async def fail_flush():
            await original_flush()
            raise SQLAlchemyError("private cancellation reason")

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(AppError) as error:
            await _cancel(env, appointment_id, db)
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert "private cancellation reason" not in error.value.message
        assert not db.in_transaction()
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).status == "UPCOMING"
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_staff_cancel_lost_commit_ack_replays_original_receipt(availability_env, monkeypatch):
    env = availability_env
    appointment_id = UUID(int=305)
    await _seed(
        env,
        appointment_id=appointment_id,
        staff_id=env.staff_id,
        starts_at=datetime(2026, 10, 17, 18, tzinfo=UTC),
    )

    async def before_cutoff(db):
        return datetime(2026, 10, 17, 16, tzinfo=UTC)

    monkeypatch.setattr(cancellation.users, "utc_now", before_cutoff)
    original_commit = AsyncSessionTransaction.commit
    commits = []
    invalidations = []

    async def lost_ack(transaction):
        commits.append(True)
        await original_commit(transaction)
        raise OperationalError("COMMIT", {}, Exception(2013, "private driver detail"))

    async with env.factory() as db:
        original_invalidate = db.invalidate

        async def invalidate():
            invalidations.append(True)
            await original_invalidate()

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSessionTransaction, "commit", lost_ack)
            patch.setattr(db, "invalidate", invalidate)
            with pytest.raises(AppError) as error:
                await _cancel(env, appointment_id, db)
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert commits == [True]
        assert invalidations == [True]

    result = await _cancel(env, appointment_id)
    assert result.status == "CANCELLED"
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert row.cancel_reason == "private staff reason"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "APPOINTMENT_CANCELLED")
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.appointment_id == appointment_id)
            )
            == 2
        )
