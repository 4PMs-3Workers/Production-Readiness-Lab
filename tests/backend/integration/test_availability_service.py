"""Availability orchestration contracts and MySQL-backed behavior."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    ServiceHour,
    ServiceSetting,
    Session,
    StaffPurpose,
    StaffUnavailability,
    User,
)
from app.repositories import availability
from app.schemas.availability import AvailabilityQuery
from app.services import availability as service

DAY = date(2026, 10, 16)
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
SELECTED_START = datetime(2026, 10, 16, 8, 10, tzinfo=ZONE).astimezone(UTC)


class CountingClock:
    def __init__(self, value: datetime = NOW):
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


class _FakeTransaction:
    def __init__(self, session):
        self.session = session
        self.active = False

    @property
    def is_active(self):
        return self.active

    async def start(self, is_ctxmanager: bool = False):
        self.active = True
        self.session._active = True
        return self

    async def commit(self):
        if self.active:
            self.session.commits += 1
            self.active = False
            self.session._active = False

    async def rollback(self):
        if self.active:
            self.session.rollbacks += 1
            self.active = False
            self.session._active = False

    async def __aenter__(self):
        await self.start(is_ctxmanager=True)
        return self

    async def __aexit__(self, error_type, error, traceback):
        if not self.active:
            return
        if error_type is None:
            await self.commit()
        else:
            await self.rollback()


class _FakeSession:
    def __init__(self):
        self._active = False
        self.commits = 0
        self.rollbacks = 0

    def begin(self):
        return _FakeTransaction(self)

    def in_transaction(self):
        return self._active

    async def rollback(self):
        self.rollbacks += 1
        self._active = False


def _as_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, session)


def _install_fake_repositories(monkeypatch, *, timezone: str = "Asia/Ho_Chi_Minh"):
    student_id, staff_id, purpose_id = uuid4(), uuid4(), uuid4()
    row = ServiceSetting(timezone=timezone, start_step_minutes=15, booking_horizon_days=7)
    purpose = Purpose(id=purpose_id, code="TEST", name="Test", duration_minutes=30)

    async def read_settings(db):
        return row

    async def find_active_purpose(db, value):
        return purpose if value == purpose_id else None

    async def find_accepting_staff(db, value_purpose, value_staff):
        return staff_id if value_purpose == purpose_id and value_staff == staff_id else None

    async def read_windows(db, weekday):
        from app.domain.availability import ServiceWindow

        return (ServiceWindow(time(8, 10), time(9, 10)),)

    async def read_empty(db, **kwargs):
        return ()

    monkeypatch.setattr(availability, "read_settings", read_settings)
    monkeypatch.setattr(availability, "read_windows", read_windows)
    monkeypatch.setattr(availability, "read_unavailability", read_empty)
    monkeypatch.setattr(availability, "read_staff_appointments", read_empty)
    monkeypatch.setattr(availability, "read_student_appointments", read_empty)
    monkeypatch.setattr(service.catalog, "find_active_purpose", find_active_purpose)
    monkeypatch.setattr(service.catalog, "find_accepting_staff", find_accepting_staff)
    return student_id, staff_id, purpose_id, row


def _fake_settings(auth_settings, *, timeout: float = 1):
    return auth_settings.model_copy(update={"db_query_timeout_seconds": timeout})


async def test_service_samples_clock_once_and_returns_pure_rule_output(monkeypatch, auth_settings):
    student_id, staff_id, purpose_id, _ = _install_fake_repositories(monkeypatch)
    clock = CountingClock()
    db = _FakeSession()
    result = await service.get_availability(
        _as_session(db),
        AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
        student_id=student_id,
        settings=_fake_settings(auth_settings),
        clock=clock,
    )
    assert clock.calls == 1
    assert result.model_dump(mode="json")["slots"] == [
        {"starts_at": "2026-10-16T01:10:00Z", "ends_at": "2026-10-16T01:40:00Z"},
        {"starts_at": "2026-10-16T01:25:00Z", "ends_at": "2026-10-16T01:55:00Z"},
        {"starts_at": "2026-10-16T01:40:00Z", "ends_at": "2026-10-16T02:10:00Z"},
    ]
    assert db.commits == 0
    assert db.rollbacks == 1
    assert not db.in_transaction()


@pytest.mark.parametrize("bad_timezone", ["", "Mars/Olympus"])
async def test_missing_or_invalid_configuration_maps_to_sanitized_503(
    monkeypatch, auth_settings, bad_timezone
):
    student_id, staff_id, purpose_id, row = _install_fake_repositories(monkeypatch)
    row.timezone = bad_timezone
    clock = CountingClock()
    db = _FakeSession()
    with pytest.raises(AppError) as caught:
        await service.get_availability(
            _as_session(db),
            AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
            student_id=student_id,
            settings=_fake_settings(auth_settings),
            clock=clock,
        )
    assert (caught.value.status_code, caught.value.code, caught.value.error_class) == (
        503,
        "SERVICE_UNAVAILABLE",
        "service",
    )
    assert not db.in_transaction()


async def test_missing_settings_never_falls_back_to_empty_availability(monkeypatch, auth_settings):
    student_id, staff_id, purpose_id, _ = _install_fake_repositories(monkeypatch)

    async def missing_settings(db):
        return None

    monkeypatch.setattr(availability, "read_settings", missing_settings)
    db = _FakeSession()
    with pytest.raises(AppError) as caught:
        await service.get_availability(
            _as_session(db),
            AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
            student_id=student_id,
            settings=_fake_settings(auth_settings),
            clock=CountingClock(),
        )
    assert (caught.value.status_code, caught.value.code, caught.value.error_class) == (
        503,
        "SERVICE_UNAVAILABLE",
        "service",
    )
    assert not db.in_transaction()


@pytest.mark.parametrize("missing", ["purpose", "staff"])
async def test_ineligible_entities_map_to_not_found(monkeypatch, auth_settings, missing):
    student_id, staff_id, purpose_id, _ = _install_fake_repositories(monkeypatch)
    if missing == "purpose":

        async def find_active_purpose(db, value):
            return None

        monkeypatch.setattr(service.catalog, "find_active_purpose", find_active_purpose)
    else:

        async def find_accepting_staff(db, value_purpose, value_staff):
            return None

        monkeypatch.setattr(service.catalog, "find_accepting_staff", find_accepting_staff)
    with pytest.raises(AppError) as caught:
        await service.get_availability(
            _as_session(_FakeSession()),
            AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
            student_id=student_id,
            settings=_fake_settings(auth_settings),
            clock=CountingClock(),
        )
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")


async def test_out_of_horizon_date_maps_to_validation_error(monkeypatch, auth_settings):
    student_id, staff_id, purpose_id, _ = _install_fake_repositories(monkeypatch)
    with pytest.raises(AppError) as caught:
        await service.get_availability(
            _as_session(_FakeSession()),
            AvailabilityQuery(
                purpose_id=purpose_id, staff_id=staff_id, date=DAY + timedelta(days=7)
            ),
            student_id=student_id,
            settings=_fake_settings(auth_settings),
            clock=CountingClock(),
        )
    assert (caught.value.status_code, caught.value.code, caught.value.error_class) == (
        422,
        "VALIDATION_ERROR",
        "input",
    )


async def test_sqlalchemy_failure_and_query_timeout_rollback_to_503(monkeypatch, auth_settings):
    student_id, staff_id, purpose_id, _ = _install_fake_repositories(monkeypatch)

    async def fail_read(db):
        raise SQLAlchemyError("private database detail")

    monkeypatch.setattr(availability, "read_settings", fail_read)
    db = _FakeSession()
    with pytest.raises(AppError) as caught:
        await service.get_availability(
            _as_session(db),
            AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
            student_id=student_id,
            settings=_fake_settings(auth_settings),
            clock=CountingClock(),
        )
    assert (caught.value.status_code, caught.value.code, caught.value.error_class) == (
        503,
        "SERVICE_UNAVAILABLE",
        "service",
    )
    assert db.rollbacks == 1
    assert not db.in_transaction()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_read(db):
        entered.set()
        await release.wait()
        return ServiceSetting(
            timezone="Asia/Ho_Chi_Minh", start_step_minutes=15, booking_horizon_days=7
        )

    monkeypatch.setattr(availability, "read_settings", slow_read)
    db = _FakeSession()
    task = asyncio.create_task(
        service.get_availability(
            _as_session(db),
            AvailabilityQuery(purpose_id=purpose_id, staff_id=staff_id, date=DAY),
            student_id=student_id,
            settings=_fake_settings(auth_settings, timeout=0.05),
            clock=CountingClock(),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(AppError) as caught:
        await task
    assert (caught.value.status_code, caught.value.code, caught.value.error_class) == (
        503,
        "SERVICE_UNAVAILABLE",
        "service",
    )
    assert db.rollbacks == 1
    assert not db.in_transaction()


async def _mysql_service(env, auth_settings, *, day: date = DAY, clock=None):
    clock = clock or CountingClock()
    async with env.factory() as db:
        try:
            result = await service.get_availability(
                db,
                AvailabilityQuery(purpose_id=env.purpose_id, staff_id=env.staff_id, date=day),
                student_id=env.student_id,
                settings=auth_settings,
                clock=clock,
            )
            assert not db.in_transaction()
            return result, clock
        except BaseException:
            assert not db.in_transaction()
            raise


def _slot_starts(result):
    return [slot.starts_at.astimezone(UTC) for slot in result.slots]


async def _add_appointment(
    env,
    *,
    student_id=None,
    staff_id=None,
    purpose_id=None,
    start=SELECTED_START,
    status="UPCOMING",
):
    ends = start + timedelta(minutes=30)
    values = {
        "student_id": student_id or env.student_id,
        "staff_id": staff_id or env.staff_id,
        "purpose_id": purpose_id or env.purpose_id,
        "duration_minutes": 30,
        "starts_at": start,
        "ends_at": ends,
        "status": status,
        "creation_request_key": uuid4().hex,
        "creation_request_hash": b"a" * 32,
    }
    if status == "CANCELLED":
        values["cancelled_at"] = NOW
    if status in {"COMPLETED", "NO_SHOW"}:
        values["outcome_at"] = NOW
    appointment = Appointment(**values)
    async with env.factory.begin() as db:
        db.add(appointment)
        await db.flush()
    return appointment


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("UPCOMING", [SELECTED_START + timedelta(minutes=30)]),
        ("CHANGE_PENDING", [SELECTED_START + timedelta(minutes=30)]),
        ("CANCELLED", [SELECTED_START + timedelta(minutes=offset) for offset in (0, 15, 30)]),
        ("COMPLETED", [SELECTED_START + timedelta(minutes=offset) for offset in (0, 15, 30)]),
        ("NO_SHOW", [SELECTED_START + timedelta(minutes=offset) for offset in (0, 15, 30)]),
    ],
)
async def test_only_upcoming_and_change_pending_appointments_block(
    availability_env, auth_settings, status, expected
):
    await _add_appointment(availability_env, status=status)
    result, clock = await _mysql_service(availability_env, auth_settings)
    assert _slot_starts(result) == expected
    assert clock.calls == 1


async def test_staff_conflict_across_purposes_and_student_conflict_across_staff_block(
    availability_env, auth_settings
):
    env = availability_env
    await _add_appointment(env, purpose_id=env.other_purpose_id)
    result, _ = await _mysql_service(env, auth_settings)
    assert _slot_starts(result) == [SELECTED_START + timedelta(minutes=30)]


async def test_student_conflict_on_other_staff_blocks_and_unrelated_rows_do_not(
    availability_env, auth_settings
):
    env = availability_env
    await _add_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
    )
    result, _ = await _mysql_service(env, auth_settings)
    assert _slot_starts(result) == [SELECTED_START + timedelta(minutes=30)]


async def test_other_student_and_staff_busy_data_do_not_block_selected_pair(
    availability_env, auth_settings
):
    env = availability_env
    await _add_appointment(
        env,
        student_id=env.other_student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
    )
    async with env.factory.begin() as db:
        db.add(
            StaffUnavailability(
                staff_id=env.other_staff_id,
                starts_at=SELECTED_START,
                ends_at=SELECTED_START + timedelta(minutes=30),
            )
        )
    result, _ = await _mysql_service(env, auth_settings)
    assert _slot_starts(result) == [
        SELECTED_START + timedelta(minutes=offset) for offset in (0, 15, 30)
    ]


async def test_selected_staff_unavailability_blocks_matching_slot(availability_env, auth_settings):
    env = availability_env
    async with env.factory.begin() as db:
        db.add(
            StaffUnavailability(
                staff_id=env.staff_id,
                starts_at=SELECTED_START,
                ends_at=SELECTED_START + timedelta(minutes=30),
            )
        )
    result, _ = await _mysql_service(env, auth_settings)
    assert _slot_starts(result) == [SELECTED_START + timedelta(minutes=30)]


@pytest.mark.parametrize("expired", [False, True])
async def test_pending_proposal_leaves_original_time_occupied_only(
    availability_env, auth_settings, expired
):
    env = availability_env
    appointment = await _add_appointment(env, status="CHANGE_PENDING")
    created = datetime(2026, 10, 14, tzinfo=UTC)
    expires = datetime(2026, 10, 15, 0, tzinfo=UTC) if expired else NOW + timedelta(days=1)
    async with env.factory.begin() as db:
        db.add(
            ChangeProposal(
                appointment_id=appointment.id,
                created_by=env.staff_id,
                proposed_starts_at=SELECTED_START + timedelta(minutes=30),
                proposed_ends_at=SELECTED_START + timedelta(minutes=60),
                status="PENDING",
                created_at=created,
                expires_at=expires,
            )
        )
    result, _ = await _mysql_service(env, auth_settings)
    assert _slot_starts(result) == [SELECTED_START + timedelta(minutes=30)]


async def test_date_window_and_non_service_day_results(availability_env, auth_settings):
    for day in (DAY - timedelta(days=1), DAY + timedelta(days=7)):
        with pytest.raises(AppError) as caught:
            await _mysql_service(availability_env, auth_settings, day=day)
        assert (caught.value.status_code, caught.value.code) == (422, "VALIDATION_ERROR")
    result, _ = await _mysql_service(availability_env, auth_settings, day=DAY + timedelta(days=1))
    assert result.slots == []


async def test_inactive_purpose_and_nonaccepting_staff_return_not_found(
    availability_env, auth_settings
):
    env = availability_env
    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        assert purpose is not None
        purpose.is_active = False
    with pytest.raises(AppError) as caught:
        await _mysql_service(env, auth_settings)
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")

    async with env.factory.begin() as db:
        purpose = await db.get(Purpose, env.purpose_id)
        assert purpose is not None
        purpose.is_active = True
        association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert association is not None
        association.is_accepting = False
    with pytest.raises(AppError) as caught:
        await _mysql_service(env, auth_settings)
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")

    async with env.factory.begin() as db:
        association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
        assert association is not None
        association.is_accepting = True
        staff = await db.get(User, env.staff_id)
        assert staff is not None
        staff.is_active = False
    with pytest.raises(AppError) as caught:
        await _mysql_service(env, auth_settings)
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")

    async with env.factory.begin() as db:
        staff = await db.get(User, env.staff_id)
        assert staff is not None
        staff.is_active = True
        staff.role = "STUDENT"
    with pytest.raises(AppError) as caught:
        await _mysql_service(env, auth_settings)
    assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")


async def test_unknown_staff_returns_not_found(availability_env, auth_settings):
    env = availability_env
    async with env.factory() as db:
        with pytest.raises(AppError) as caught:
            await service.get_availability(
                db,
                AvailabilityQuery(purpose_id=env.purpose_id, staff_id=uuid4(), date=DAY),
                student_id=env.student_id,
                settings=auth_settings,
                clock=CountingClock(),
            )
        assert (caught.value.status_code, caught.value.code) == (404, "NOT_FOUND")
        assert not db.in_transaction()


async def test_query_count_is_constant_and_service_is_read_only(availability_env, auth_settings):
    env = availability_env
    statements: list[str] = []
    engine = env.factory.kw["bind"].sync_engine

    async def measured(step):
        async with env.factory.begin() as db:
            setting = await db.get(ServiceSetting, 1)
            assert setting is not None
            setting.start_step_minutes = step
        statements.clear()

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            result, _ = await _mysql_service(env, auth_settings)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert result.slots
        count = sum(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert not any(
            sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
            or "FOR UPDATE" in sql.upper()
            or "FOR SHARE" in sql.upper()
            for sql in statements
        )
        return count

    counts = [await measured(step) for step in (15, 1)]
    assert counts[0] == counts[1] <= 7


async def test_three_appointments_on_other_days_do_not_apply_a_quota(
    availability_env, auth_settings
):
    env = availability_env
    for offset in (1, 2, 3):
        await _add_appointment(
            env,
            start=datetime(2026, 10, 16 + offset, 8, 10, tzinfo=ZONE).astimezone(UTC),
        )
    result, _ = await _mysql_service(env, auth_settings)
    assert result.slots


async def test_service_leaves_all_eleven_application_tables_unchanged(
    availability_env, auth_settings
):
    env = availability_env
    tables = (
        User,
        Session,
        Purpose,
        StaffPurpose,
        ServiceSetting,
        ServiceHour,
        StaffUnavailability,
        Appointment,
        ChangeProposal,
        Notification,
        AuditEvent,
    )

    async def row_counts():
        async with env.factory() as db:
            snapshots = []
            for model in tables:
                result = await db.execute(select(*model.__table__.columns))
                rows = [tuple(row) for row in result]
                snapshots.append(tuple(sorted(rows, key=repr)))
            return snapshots

    before = await row_counts()
    await _mysql_service(env, auth_settings)
    assert await row_counts() == before
