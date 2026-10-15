"""Bounded availability repository reads against the migrated MySQL schema."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from app.domain.availability import TimeInterval
from app.models import Appointment, ServiceSetting, StaffUnavailability
from app.repositories import availability

pytestmark = pytest.mark.mysql
DAY = date(2026, 10, 16)  # Friday in the fixture's Ho Chi Minh business timezone.
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def day_bounds() -> TimeInterval:
    lower = datetime.combine(DAY, time.min, ZONE).astimezone(UTC)
    upper = datetime.combine(DAY + timedelta(days=1), time.min, ZONE).astimezone(UTC)
    return TimeInterval(lower, upper)


async def add_appointment(
    env,
    *,
    student_id=None,
    staff_id=None,
    purpose_id=None,
    starts_at: datetime,
    ends_at: datetime,
    status: str = "UPCOMING",
) -> Appointment:
    values = {
        "student_id": student_id or env.student_id,
        "staff_id": staff_id or env.staff_id,
        "purpose_id": purpose_id or env.purpose_id,
        "duration_minutes": int((ends_at - starts_at).total_seconds() // 60),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "status": status,
        "creation_request_key": uuid4().hex,
        "creation_request_hash": b"h" * 32,
    }
    if status == "CANCELLED":
        values["cancelled_at"] = datetime(2026, 10, 14, tzinfo=UTC)
    if status in {"COMPLETED", "NO_SHOW"}:
        values["outcome_at"] = datetime(2026, 10, 14, tzinfo=UTC)
    appointment = Appointment(**values)
    async with env.factory.begin() as db:
        db.add(appointment)
        await db.flush()
    return appointment


async def test_repository_busy_reads_include_crossing_intervals_and_only_blocking_statuses(
    availability_env,
):
    env = availability_env
    bounds = day_bounds()
    start = bounds.starts_at
    end = bounds.ends_at
    await add_appointment(
        env,
        staff_id=env.staff_id,
        purpose_id=env.other_purpose_id,
        starts_at=start - timedelta(minutes=20),
        ends_at=end + timedelta(minutes=20),
    )
    await add_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
        starts_at=end - timedelta(minutes=10),
        ends_at=end + timedelta(minutes=20),
        status="CHANGE_PENDING",
    )
    await add_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
        starts_at=start - timedelta(minutes=5),
        ends_at=start,
    )
    await add_appointment(
        env,
        student_id=env.other_student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
        starts_at=start - timedelta(minutes=5),
        ends_at=start,
    )
    await add_appointment(
        env,
        student_id=env.other_student_id,
        staff_id=env.staff_id,
        purpose_id=env.purpose_id,
        starts_at=end,
        ends_at=end + timedelta(minutes=5),
    )
    async with availability_env.factory() as db:
        staff = await availability.read_staff_appointments(db, staff_id=env.staff_id, bounds=bounds)
        student = await availability.read_student_appointments(
            db, student_id=env.student_id, bounds=bounds
        )
        unavailable = await availability.read_unavailability(
            db, staff_id=env.staff_id, bounds=bounds
        )
        await db.rollback()
    full_day = TimeInterval(start - timedelta(minutes=20), end + timedelta(minutes=20))
    assert staff == (full_day,)
    assert student == (
        full_day,
        TimeInterval(end - timedelta(minutes=10), end + timedelta(minutes=20)),
    )
    assert unavailable == ()


async def test_unavailability_query_is_staff_scoped_and_half_open(availability_env):
    env = availability_env
    bounds = day_bounds()
    lower, upper = bounds.starts_at, bounds.ends_at
    rows = [
        StaffUnavailability(
            staff_id=env.staff_id,
            starts_at=lower - timedelta(hours=1),
            ends_at=upper + timedelta(hours=1),
        ),
        StaffUnavailability(
            staff_id=env.staff_id,
            starts_at=lower - timedelta(hours=1),
            ends_at=lower,
        ),
        StaffUnavailability(
            staff_id=env.staff_id,
            starts_at=upper,
            ends_at=upper + timedelta(hours=1),
        ),
        StaffUnavailability(
            staff_id=env.other_staff_id,
            starts_at=lower,
            ends_at=upper,
        ),
    ]
    async with env.factory.begin() as db:
        db.add_all(rows)
    async with env.factory() as db:
        result = await availability.read_unavailability(db, staff_id=env.staff_id, bounds=bounds)
        await db.rollback()
    assert result == (TimeInterval(lower - timedelta(hours=1), upper + timedelta(hours=1)),)


async def test_settings_and_windows_are_read_without_creating_fallback_rows(availability_env):
    env = availability_env
    async with env.factory() as db:
        settings = await availability.read_settings(db)
        windows = await availability.read_windows(db, weekday=5)
        assert settings is not None
        assert (settings.timezone, settings.start_step_minutes, settings.booking_horizon_days) == (
            "Asia/Ho_Chi_Minh",
            15,
            7,
        )
        assert [(window.opens_at, window.closes_at) for window in windows] == [
            (time(8, 10), time(9, 10))
        ]
        await db.rollback()
    async with env.factory.begin() as db:
        await db.delete(await db.get(ServiceSetting, 1))
    async with env.factory() as db:
        assert await availability.read_settings(db) is None
        await db.rollback()
    async with env.factory() as db:
        assert await db.get(ServiceSetting, 1) is None
        await db.rollback()


async def test_appointment_projection_does_not_read_private_consultation_fields(availability_env):
    env = availability_env
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(env.factory.kw["bind"].sync_engine, "before_cursor_execute", capture)
    try:
        async with env.factory() as db:
            await availability.read_staff_appointments(
                db, staff_id=env.staff_id, bounds=day_bounds()
            )
            await db.rollback()
    finally:
        event.remove(env.factory.kw["bind"].sync_engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    sql = statements[0].lower()
    assert "select appointments.starts_at, appointments.ends_at" in sql
    assert "other_reason" not in sql
    assert "location" not in sql
    assert "password" not in sql
    assert "session" not in sql
