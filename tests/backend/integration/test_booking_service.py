"""MySQL-backed booking transaction, concurrency, and recovery contracts."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select

from app.api.errors import AppError
from app.domain.auth import AuthContext
from app.models import (
    Appointment,
    AuditEvent,
    Notification,
    Purpose,
    StaffPurpose,
    User,
)
from app.repositories import users
from app.schemas.appointments import AppointmentCreateRequest
from app.services import booking as service

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def _context(student_id):
    return AuthContext(
        session_id=uuid4(),
        user_id=student_id,
        email="student@example.invalid",
        name="Test Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _payload(env, start, *, description=None):
    return AppointmentCreateRequest(
        purpose_id=env.purpose_id,
        staff_id=env.staff_id,
        starts_at=start,
        description=description,
    )


async def _create(env, payload, settings, *, key=None, student_id=None, db=None):
    key = key or "booking-" + uuid4().hex
    actor_id = student_id or env.student_id
    request_id = "request-" + uuid4().hex
    if db is not None:
        return await service.create_appointment(
            db,
            payload,
            context=_context(actor_id),
            idempotency_key=key,
            request_id=request_id,
            settings=settings,
            deadline=asyncio.get_running_loop().time() + settings.request_timeout_seconds,
        )
    async with env.factory() as session:
        return await _create(
            env,
            payload,
            settings,
            key=key,
            student_id=actor_id,
            db=session,
        )


async def _seed_appointment(
    env,
    *,
    student_id,
    staff_id,
    purpose_id,
    starts_at,
    status="UPCOMING",
    key=None,
):
    duration = 30
    values = {
        "student_id": student_id,
        "staff_id": staff_id,
        "purpose_id": purpose_id,
        "duration_minutes": duration,
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(minutes=duration),
        "status": status,
        "creation_request_key": key or "seed-" + uuid4().hex,
        "creation_request_hash": bytes(32),
    }
    if status == "CANCELLED":
        values["cancelled_at"] = datetime.now(UTC)
    async with env.factory.begin() as db:
        row = Appointment(**values)
        db.add(row)
        await db.flush()
        return row.id


async def _row_count(factory, model):
    async with factory() as db:
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_creation_commits_appointment_audit_and_two_private_notifications(
    booking_env, auth_settings
):
    env, _, start = booking_env
    description = "  private consultation detail  "
    receipt, replayed = await _create(
        env, _payload(env, start, description=description), auth_settings
    )

    assert replayed is False
    assert receipt.status == "UPCOMING"
    async with env.factory() as db:
        appointment = await db.get(Appointment, receipt.id)
        assert appointment is not None
        assert appointment.student_id == env.student_id
        assert appointment.starts_at == start
        assert appointment.ends_at == start + timedelta(minutes=30)
        assert appointment.duration_minutes == 30
        assert appointment.other_reason == description
        audit = await db.scalar(select(AuditEvent).where(AuditEvent.entity_id == str(receipt.id)))
        assert audit is not None
        assert audit.actor_id == env.student_id
        assert audit.action == "APPOINTMENT_CREATED"
        assert audit.event_metadata == {
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "duration_minutes": 30,
        }
        assert description not in repr(audit.event_metadata)
        notifications = list(
            (
                await db.scalars(
                    select(Notification).where(Notification.appointment_id == receipt.id)
                )
            ).all()
        )
        assert {item.recipient_id for item in notifications} == {env.student_id, env.staff_id}
        assert len(notifications) == 2
        assert {item.event_type for item in notifications} == {"APPOINTMENT_CREATED"}


async def test_same_key_replays_original_receipt_after_current_status_changes(
    booking_env, auth_settings
):
    env, _, start = booking_env
    payload = _payload(env, start)
    original, replayed = await _create(env, payload, auth_settings, key="stable-key")
    assert replayed is False
    async with env.factory.begin() as db:
        row = await db.get(Appointment, original.id)
        assert row is not None
        row.status = "CANCELLED"
        row.cancelled_at = await users.utc_now(db)

    replay, replayed = await _create(env, payload, auth_settings, key="stable-key")
    assert replayed is True
    assert replay == original
    assert replay.status == "UPCOMING"
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_same_key_with_different_content_is_an_idempotency_conflict(
    booking_env, auth_settings
):
    env, _, start = booking_env
    await _create(env, _payload(env, start), auth_settings, key="different-content")

    with pytest.raises(AppError) as caught:
        await _create(
            env,
            _payload(env, start + timedelta(minutes=15)),
            auth_settings,
            key="different-content",
        )

    assert (caught.value.status_code, caught.value.code) == (409, "IDEMPOTENCY_CONFLICT")
    assert await _row_count(env.factory, Appointment) == 1
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_inactive_purpose_staff_or_acceptance_is_rechecked_under_locks(
    booking_env, auth_settings
):
    env, _, start = booking_env
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        staff = await db.get(User, env.staff_id)
        association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert purpose is not None and staff is not None and association is not None
        purpose.is_active = False
        staff.is_active = False
        association.is_accepting = False

    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")
    assert await _row_count(env.factory, Appointment) == 0


async def test_required_description_is_checked_against_current_purpose(booking_env, auth_settings):
    env, _, start = booking_env
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        assert purpose is not None
        purpose.requires_free_text = True

    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (422, "VALIDATION_ERROR")
    assert await _row_count(env.factory, Appointment) == 0

    receipt, replayed = await _create(
        env, _payload(env, start, description="  reason as entered  "), auth_settings
    )
    assert replayed is False
    async with env.factory() as db:
        appointment = await db.get(Appointment, receipt.id)
        assert appointment is not None
        assert appointment.other_reason == "  reason as entered  "


async def test_quota_counts_only_upcoming_and_change_pending_across_all_dates(
    booking_env, auth_settings
):
    env, selected_date, start = booking_env
    await _seed_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.staff_id,
        purpose_id=env.purpose_id,
        starts_at=start - timedelta(days=4),
        status="CANCELLED",
    )
    for offset, status in ((2, "UPCOMING"), (3, "CHANGE_PENDING"), (4, "UPCOMING")):
        await _seed_appointment(
            env,
            student_id=env.student_id,
            staff_id=env.staff_id,
            purpose_id=env.purpose_id,
            starts_at=datetime.combine(
                selected_date + timedelta(days=offset), time(8), ZONE
            ).astimezone(UTC),
            status=status,
        )

    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (409, "BOOKING_QUOTA_EXCEEDED")
    assert await _row_count(env.factory, Appointment) == 4


async def test_student_and_staff_overlaps_block_but_half_open_adjacency_is_allowed(
    booking_env, auth_settings
):
    env, _, start = booking_env
    await _seed_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.purpose_id,
        starts_at=start,
    )
    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start + timedelta(minutes=15)), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (409, "BOOKING_CONFLICT")

    receipt, replayed = await _create(
        env, _payload(env, start + timedelta(minutes=30)), auth_settings
    )
    assert replayed is False
    async with env.factory() as db:
        adjacent = await db.get(Appointment, receipt.id)
        assert adjacent is not None
        assert adjacent.starts_at == start + timedelta(minutes=30)


async def test_staff_overlap_from_another_student_blocks_candidate(booking_env, auth_settings):
    env, _, start = booking_env
    await _seed_appointment(
        env,
        student_id=env.other_student_id,
        staff_id=env.staff_id,
        purpose_id=env.purpose_id,
        starts_at=start,
    )

    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (409, "BOOKING_CONFLICT")


@pytest.mark.parametrize("status", ["UPCOMING", "CHANGE_PENDING"])
async def test_only_upcoming_and_change_pending_intervals_block(booking_env, auth_settings, status):
    env, _, start = booking_env
    await _seed_appointment(
        env,
        student_id=env.other_student_id,
        staff_id=env.staff_id,
        purpose_id=env.purpose_id,
        starts_at=start,
        status=status,
    )

    with pytest.raises(AppError) as caught:
        await _create(env, _payload(env, start), auth_settings)
    assert (caught.value.status_code, caught.value.code) == (409, "BOOKING_CONFLICT")


async def test_settings_and_accounts_are_locked_before_the_separate_database_clock(
    booking_env, auth_settings
):
    env, _, start = booking_env
    statements = []
    sync_engine = env.factory.kw["bind"].sync_engine

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        normalized = statement.upper()
        statements.append((normalized, parameters))

    event.listen(sync_engine, "before_cursor_execute", capture)
    try:
        await _create(env, _payload(env, start), auth_settings)
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture)

    settings_lock = next(
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "SERVICE_SETTINGS" in statement
        and ("FOR SHARE" in statement or "LOCK IN SHARE MODE" in statement)
    )
    account_locks = [
        (index, parameters)
        for index, (statement, parameters) in enumerate(statements)
        if "FROM USERS" in statement and "FOR UPDATE" in statement
    ]
    clock_read = next(
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "SELECT UTC_TIMESTAMP(6)" in statement
    )
    assert len(account_locks) == 2
    account_ids = []
    for _, parameters in account_locks:
        values = list(parameters.values()) if isinstance(parameters, dict) else list(parameters)
        bound_id = next(value for value in values if isinstance(value, (bytes, bytearray)))
        account_ids.append(UUID(bytes=bytes(bound_id)))
    assert account_ids == sorted({env.student_id, env.staff_id}, key=lambda value: value.bytes)
    lock_indexes = [index for index, _ in account_locks]
    assert settings_lock < min(lock_indexes) < max(lock_indexes) < clock_read


async def test_same_student_concurrent_same_key_creates_one_bundle(booking_env, auth_settings):
    env, _, start = booking_env
    barrier = asyncio.Barrier(2)
    payload = _payload(env, start)

    async def submit():
        async with env.factory() as db:
            await barrier.wait()
            return await _create(env, payload, auth_settings, key="racing-key", db=db)

    outcomes = await asyncio.gather(submit(), submit())
    assert sorted(replayed for _, replayed in outcomes) == [False, True]
    assert outcomes[0][0] == outcomes[1][0]
    assert await _row_count(env.factory, Appointment) == 1
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_same_student_concurrent_same_key_different_hash_conflicts(
    booking_env, auth_settings
):
    env, _, start = booking_env
    barrier = asyncio.Barrier(2)

    async def submit(payload):
        async with env.factory() as db:
            await barrier.wait()
            try:
                return await _create(env, payload, auth_settings, key="racing-conflict", db=db)
            except AppError as error:
                return error

    outcomes = await asyncio.gather(
        submit(_payload(env, start)),
        submit(_payload(env, start + timedelta(minutes=15))),
    )
    assert sum(isinstance(outcome, AppError) for outcome in outcomes) == 1
    conflict = next(outcome for outcome in outcomes if isinstance(outcome, AppError))
    assert (conflict.status_code, conflict.code) == (409, "IDEMPOTENCY_CONFLICT")
    assert await _row_count(env.factory, Appointment) == 1
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_account_lock_serializes_the_last_quota_slot(booking_env, auth_settings):
    env, selected_date, start = booking_env
    for offset in (2, 3):
        await _seed_appointment(
            env,
            student_id=env.student_id,
            staff_id=env.other_staff_id,
            purpose_id=env.purpose_id,
            starts_at=datetime.combine(
                selected_date + timedelta(days=offset), time(8), ZONE
            ).astimezone(UTC),
        )
    barrier = asyncio.Barrier(2)

    async def submit(slot):
        async with env.factory() as db:
            await barrier.wait()
            try:
                return await _create(
                    env,
                    _payload(env, slot),
                    auth_settings,
                    key="quota-" + slot.isoformat(),
                    db=db,
                )
            except AppError as error:
                return error

    outcomes = await asyncio.gather(submit(start), submit(start + timedelta(minutes=30)))
    assert sum(isinstance(outcome, AppError) for outcome in outcomes) == 1
    rejected = next(outcome for outcome in outcomes if isinstance(outcome, AppError))
    assert (rejected.status_code, rejected.code) == (409, "BOOKING_QUOTA_EXCEEDED")
    assert await _row_count(env.factory, Appointment) == 3
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_staff_account_lock_serializes_different_students_for_one_slot(
    booking_env, auth_settings
):
    env, _, start = booking_env
    barrier = asyncio.Barrier(2)

    async def submit(student_id):
        async with env.factory() as db:
            await barrier.wait()
            try:
                return await _create(
                    env,
                    _payload(env, start),
                    auth_settings,
                    student_id=student_id,
                    key="staff-slot-" + student_id.hex,
                    db=db,
                )
            except AppError as error:
                return error

    outcomes = await asyncio.gather(submit(env.student_id), submit(env.other_student_id))
    assert sum(isinstance(outcome, AppError) for outcome in outcomes) == 1
    rejected = next(outcome for outcome in outcomes if isinstance(outcome, AppError))
    assert (rejected.status_code, rejected.code) == (409, "BOOKING_CONFLICT")
    assert await _row_count(env.factory, Appointment) == 1
    assert await _row_count(env.factory, AuditEvent) == 1
    assert await _row_count(env.factory, Notification) == 2


async def test_mysql_lock_timeout_rolls_back_then_retries_within_deadline(
    booking_env, auth_settings
):
    env, _, start = booking_env
    settings = auth_settings.model_copy(
        update={"request_timeout_seconds": 5.0, "max_deadlock_retries": 2}
    )
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()
    sync_engine = env.factory.kw["bind"].sync_engine

    def set_test_lock_timeout(connection, _record):
        cursor = connection.cursor()
        try:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 1")
        finally:
            cursor.close()

    event.listen(sync_engine, "connect", set_test_lock_timeout)

    async def hold_student_lock():
        async with env.factory() as holder:
            async with holder.begin():
                await users.lock_by_id(holder, env.student_id)
                lock_acquired.set()
                await release_lock.wait()

    holder_task = asyncio.create_task(hold_student_lock())
    try:
        await lock_acquired.wait()
        async with env.factory() as db:
            release_task = asyncio.create_task(_release_after(release_lock, 1.2))
            result = await _create(env, _payload(env, start), settings, db=db)
            await release_task
        assert result[1] is False
        assert await _row_count(env.factory, Appointment) == 1
    finally:
        release_lock.set()
        await holder_task
        event.remove(sync_engine, "connect", set_test_lock_timeout)


async def _release_after(event_to_set, seconds):
    await asyncio.sleep(seconds)
    event_to_set.set()


async def test_notification_failure_rolls_back_the_entire_write_bundle(booking_env, auth_settings):
    env, _, start = booking_env
    trigger = "reject_booking_notification"
    async with env.factory.kw["bind"].begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON notifications "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'test notification failure'"
        )
    try:
        with pytest.raises(AppError) as caught:
            await _create(env, _payload(env, start), auth_settings)
        assert caught.value.status_code == 503
        assert caught.value.error_class == "service"
        assert await _row_count(env.factory, Appointment) == 0
        assert await _row_count(env.factory, AuditEvent) == 0
        assert await _row_count(env.factory, Notification) == 0
    finally:
        async with env.factory.kw["bind"].begin() as connection:
            await connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
