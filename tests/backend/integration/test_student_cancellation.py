"""Focused cancellation contract and atomic MySQL state transitions."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_booking_service import _context, _create, _payload

from app.api.errors import AppError
from app.models import Appointment, AuditEvent, ChangeProposal, Notification, ServiceSetting, User
from app.schemas import appointments as schemas


async def cancel(env, appointment_id, settings, *, reason=None, student_id=None, db=None):
    from app.services import cancellation

    if db is None:
        async with env.factory() as session:
            return await cancel(
                env, appointment_id, settings, reason=reason, student_id=student_id, db=session
            )
    return await cancellation.cancel_appointment(
        db,
        appointment_id=appointment_id,
        payload=schemas.AppointmentCancelRequest(reason=reason),
        context=_context(student_id or env.student_id),
        request_id="cancel-test",
        settings=settings,
        deadline=asyncio.get_running_loop().time() + 5,
    )


async def seed(env, start, status="UPCOMING"):
    values = {}
    if status in {"COMPLETED", "NO_SHOW"}:
        values["outcome_at"] = start
    if status == "CANCELLED":
        values.update(
            cancelled_at=start - timedelta(days=1),
            cancelled_by=env.student_id,
            cancel_reason="original private reason",
        )
    async with env.factory.begin() as db:
        row = Appointment(
            student_id=env.student_id,
            staff_id=env.staff_id,
            purpose_id=env.purpose_id,
            duration_minutes=30,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            status=status,
            creation_request_key=uuid4().hex,
            creation_request_hash=bytes(32),
            **values,
        )
        db.add(row)
        await db.flush()
        return row.id


@pytest.mark.parametrize("status", ["UPCOMING", "CHANGE_PENDING"])
async def test_cancellation_commits_bundle_and_resolves_proposal(
    booking_env, auth_settings, status
):
    env, _, start = booking_env
    appointment_id = await seed(env, start, status)
    if status == "CHANGE_PENDING":
        async with env.factory.begin() as db:
            db.add(
                ChangeProposal(
                    appointment_id=appointment_id,
                    created_by=env.staff_id,
                    proposed_starts_at=start + timedelta(hours=1),
                    proposed_ends_at=start + timedelta(hours=2),
                    expires_at=start,
                )
            )
    receipt = await cancel(env, appointment_id, auth_settings, reason="private reason")
    assert receipt.status == "CANCELLED"
    assert set(receipt.model_dump()) == {"id", "status", "cancelled_at"}
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert row.status == "CANCELLED"
        assert row.cancelled_at == receipt.cancelled_at
        assert row.cancelled_by == env.student_id
        assert row.cancel_reason == "private reason"
        audits = (await db.scalars(select(AuditEvent))).all()
        assert len(audits) == 1
        assert audits[0].action == "APPOINTMENT_CANCELLED"
        assert audits[0].actor_id == env.student_id
        assert "private reason" not in repr(audits[0].event_metadata)
        notices = (await db.scalars(select(Notification))).all()
        assert len(notices) == 2
        assert {n.recipient_id for n in notices} == {env.student_id, env.staff_id}
        assert all(n.event_type == "APPOINTMENT_CANCELLED" for n in notices)
        if status == "CHANGE_PENDING":
            proposal = await db.scalar(select(ChangeProposal))
            assert proposal.status == "CANCELLED"
            assert proposal.resolved_at == receipt.cancelled_at
            assert proposal.resolved_by == env.student_id


@pytest.mark.parametrize("status", ["COMPLETED", "NO_SHOW"])
async def test_terminal_outcomes_conflict_without_side_effects(booking_env, auth_settings, status):
    env, _, start = booking_env
    appointment_id = await seed(env, start, status)
    with pytest.raises(AppError) as error:
        await cancel(env, appointment_id, auth_settings)
    assert (error.value.status_code, error.value.code) == (409, "APPOINTMENT_STATE_CONFLICT")
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).status == status
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.parametrize("offset,allowed", [(-1, True), (0, False), (1, False)])
async def test_cutoff_is_strict_local_midnight(
    booking_env, auth_settings, monkeypatch, offset, allowed
):
    from tests.backend.integration.test_booking_service import ZONE

    from app.repositories import users

    env, date, start = booking_env
    appointment_id = await seed(env, start)
    now = datetime.combine(date, datetime.min.time(), ZONE).astimezone(UTC)

    async def clock(_db):
        return now + timedelta(microseconds=offset)

    monkeypatch.setattr(users, "utc_now", clock)
    if allowed:
        assert (await cancel(env, appointment_id, auth_settings)).status == "CANCELLED"
    else:
        with pytest.raises(AppError) as error:
            await cancel(env, appointment_id, auth_settings)
        assert error.value.code == "CANCELLATION_DEADLINE_PASSED"


async def test_owned_cancelled_replay_preserves_original_without_writes_or_policy(
    booking_env, auth_settings
):
    env, _, start = booking_env
    appointment_id = await seed(env, start - timedelta(days=5), "CANCELLED")
    async with env.factory.begin() as db:
        (await db.get(ServiceSetting, 1)).timezone = "Invalid/Zone"
    result = await cancel(env, appointment_id, auth_settings, reason="replacement")
    async with env.factory() as db:
        row = await db.get(Appointment, appointment_id)
        assert result.cancelled_at == start - timedelta(days=6)
        assert row.cancel_reason == "original private reason"
        assert row.cancelled_by == env.student_id
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.parametrize("foreign", [False, True])
async def test_missing_and_foreign_share_404(booking_env, auth_settings, foreign):
    env, _, start = booking_env
    appointment_id = await seed(env, start) if foreign else uuid4()
    with pytest.raises(AppError) as error:
        await cancel(env, appointment_id, auth_settings, student_id=env.other_student_id)
    assert (error.value.status_code, error.value.code) == (404, "NOT_FOUND")


@pytest.mark.parametrize("change", [{"is_active": False}, {"role": "STAFF"}])
async def test_locked_actor_is_revalidated(booking_env, auth_settings, change):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    async with env.factory.begin() as db:
        actor = await db.get(User, env.student_id)
        for key, value in change.items():
            setattr(actor, key, value)
    with pytest.raises(AppError) as error:
        await cancel(env, appointment_id, auth_settings)
    assert error.value.status_code == 404


async def test_invalid_settings_fail_closed_for_new_cancellation(booking_env, auth_settings):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    async with env.factory.begin() as db:
        (await db.get(ServiceSetting, 1)).timezone = "Invalid/Zone"
    with pytest.raises(AppError) as error:
        await cancel(env, appointment_id, auth_settings)
    assert (error.value.status_code, error.value.code) == (503, "SERVICE_UNAVAILABLE")


async def test_booking_receipt_survives_cancellation(booking_env, auth_settings):
    env, _, start = booking_env
    payload = _payload(env, start)
    receipt, _ = await _create(env, payload, auth_settings, key="immutable-receipt")
    await cancel(env, receipt.id, auth_settings)
    replay, replayed = await _create(env, payload, auth_settings, key="immutable-receipt")
    assert replayed and replay == receipt


async def test_precommit_failure_rolls_back_entire_cancellation(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
    async with env.factory() as db:
        original_flush = db.flush

        async def fail_flush():
            await original_flush()
            raise SQLAlchemyError("private reason must not escape")

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(AppError) as error:
            await cancel(env, appointment_id, auth_settings, db=db, reason="private reason")
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert "private reason" not in error.value.message
        assert not db.in_transaction()
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).status == "UPCOMING"
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_lost_commit_ack_returns_503_without_retry_and_replay_recovers(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    appointment_id = await seed(env, start)
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
                await cancel(env, appointment_id, auth_settings, db=db, reason="original")
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert commits == [True]
        assert invalidations == [True]
    result = await cancel(env, appointment_id, auth_settings, reason="replacement")
    assert result.status == "CANCELLED"
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).cancel_reason == "original"
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert await db.scalar(select(func.count()).select_from(Notification)) == 2


@pytest.mark.parametrize(
    "budget", ["db_lock_timeout_seconds", "db_query_timeout_seconds", "request_timeout_seconds"]
)
async def test_cancellation_budgets_abort_without_writes(
    booking_env, auth_settings, monkeypatch, budget
):
    from app.repositories import appointments
    from app.services import cancellation

    env, _, start = booking_env
    appointment_id = await seed(env, start)

    async def blocked(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(appointments, "lock_service_settings", blocked)
    settings = auth_settings.model_copy(update={budget: 0.02})
    async with env.factory() as db:
        with pytest.raises(AppError) as error:
            await cancellation.cancel_appointment(
                db,
                appointment_id=appointment_id,
                payload=schemas.AppointmentCancelRequest(),
                context=_context(env.student_id),
                request_id="budget",
                settings=settings,
                deadline=asyncio.get_running_loop().time() + settings.request_timeout_seconds,
            )
        assert error.value.code == "SERVICE_UNAVAILABLE"
        assert not db.in_transaction()
    async with env.factory() as db:
        assert (await db.get(Appointment, appointment_id)).status == "UPCOMING"
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_lock_sql_orders_settings_sorted_users_appointment_proposal_then_clock(
    booking_env, auth_settings
):
    env, _, start = booking_env
    appointment_id = await seed(env, start, "CHANGE_PENDING")
    async with env.factory.begin() as db:
        db.add(
            ChangeProposal(
                appointment_id=appointment_id,
                created_by=env.staff_id,
                proposed_starts_at=start + timedelta(hours=1),
                proposed_ends_at=start + timedelta(hours=2),
                expires_at=start,
            )
        )
    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        await cancel(env, appointment_id, auth_settings)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    locks = [
        (sql, params)
        for sql, params in statements
        if "FOR UPDATE" in sql or "LOCK IN SHARE MODE" in sql
    ]
    assert "service_settings" in locks[0][0] and "LOCK IN SHARE MODE" in locks[0][0]
    assert all("users" in sql for sql, _ in locks[1:3])
    assert [params[0] for _, params in locks[1:3]] == sorted(
        [env.student_id.bytes, env.staff_id.bytes]
    )
    assert "appointments" in locks[3][0]
    assert "change_proposals" in locks[4][0]
    assert len(locks) == 5
    clock_indexes = [i for i, (sql, _) in enumerate(statements) if "UTC_TIMESTAMP(6)" in sql]
    assert len(clock_indexes) == 1
    assert statements.index(locks[-1]) < clock_indexes[0]
