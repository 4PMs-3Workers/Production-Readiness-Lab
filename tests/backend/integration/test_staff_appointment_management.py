"""MySQL-backed assigned appointment history and privacy behavior."""

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from app.api.errors import AppError
from app.models import Appointment, Purpose, StaffPurpose, User
from app.schemas.staff_appointments import StaffAppointmentListQuery
from app.services import staff_appointments

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
