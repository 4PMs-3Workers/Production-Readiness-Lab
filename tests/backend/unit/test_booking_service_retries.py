"""DB-independent checks for the service's retry and duplicate-resolution boundary."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.errors import AppError
from app.domain.auth import AuthContext
from app.domain.availability import ServiceWindow
from app.models import Appointment, AuditEvent, Notification, Purpose, ServiceSetting, User
from app.schemas.appointments import AppointmentCreateRequest, AppointmentCreateResponse
from app.services import booking


class CleanSession:
    def in_transaction(self):
        return False

    async def rollback(self):
        return None


class TransactionSession(CleanSession):
    def __init__(self, *, commit_error=None):
        self.active = False
        self.commits = 0
        self.rollbacks = 0
        self.invalidations = 0
        self.commit_error = commit_error
        self.added = []

    def begin(self):
        return FakeTransaction(self)

    async def flush(self):
        for row in self.added:
            if isinstance(row, Appointment):
                row.created_at = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)

    def in_transaction(self):
        return self.active

    async def rollback(self):
        self.rollbacks += 1
        self.active = False

    async def invalidate(self):
        self.invalidations += 1
        self.active = False


class FakeTransaction:
    def __init__(self, session):
        self.session = session

    def __await__(self):
        async def start():
            self.session.active = True
            return self

        return start().__await__()

    async def __aenter__(self):
        self.session.active = True
        return self

    async def __aexit__(self, error_type, _error, _traceback):
        if error_type is None:
            await self.commit()
        else:
            await self.rollback()

    async def commit(self):
        self.session.commits += 1
        if self.session.commit_error is not None:
            raise self.session.commit_error
        self.session.active = False

    async def rollback(self):
        await self.session.rollback()


def _request() -> AppointmentCreateRequest:
    return AppointmentCreateRequest(
        purpose_id=uuid4(),
        staff_id=uuid4(),
        starts_at=datetime(2026, 10, 16, 1, 0, tzinfo=UTC),
    )


def _context() -> AuthContext:
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="student@example.invalid",
        name="Student",
        role="STUDENT",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _retryable_lock_error(code: int) -> OperationalError:
    return OperationalError("SELECT ... FOR UPDATE", {}, Exception(code, "retryable lock failure"))


@pytest.mark.parametrize("mysql_error_code", [1205, 1213], ids=["lock-timeout", "deadlock"])
async def test_mysql_lock_errors_retry_a_fresh_attempt(
    monkeypatch, auth_settings, mysql_error_code
):
    attempts = 0
    payload = _request()
    context = _context()
    receipt = AppointmentCreateResponse(
        id=uuid4(), status="UPCOMING", created_at=datetime(2026, 10, 1, tzinfo=UTC)
    )

    async def no_committed_row(*_args, **_kwargs):
        return None

    async def retry_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _retryable_lock_error(mysql_error_code)
        return receipt, False

    monkeypatch.setattr(booking, "_read_committed_key", no_committed_row)
    monkeypatch.setattr(booking, "_run_attempt_with_commit_safety", retry_once)
    settings = auth_settings.model_copy(update={"max_deadlock_retries": 2})
    result = await booking.create_appointment(
        CleanSession(),
        payload,
        context=context,
        idempotency_key="retryable-key",
        request_id="request-1",
        settings=settings,
        deadline=asyncio.get_running_loop().time() + 2,
    )

    assert result == (receipt, False)
    assert attempts == 2


async def test_retry_budget_is_bounded_and_database_failure_stays_service_error(
    monkeypatch, auth_settings
):
    attempts = 0

    async def no_committed_row(*_args, **_kwargs):
        return None

    async def always_times_out(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise _retryable_lock_error(1205)

    monkeypatch.setattr(booking, "_read_committed_key", no_committed_row)
    monkeypatch.setattr(booking, "_run_attempt_with_commit_safety", always_times_out)
    settings = auth_settings.model_copy(update={"max_deadlock_retries": 2})

    with pytest.raises(AppError) as caught:
        await booking.create_appointment(
            CleanSession(),
            _request(),
            context=_context(),
            idempotency_key="bounded-retry-key",
            request_id="request-2",
            settings=settings,
            deadline=asyncio.get_running_loop().time() + 2,
        )

    assert (caught.value.status_code, caught.value.code) == (503, "SERVICE_UNAVAILABLE")
    assert attempts == 3


async def test_only_the_named_idempotency_unique_constraint_is_resolved_as_a_replay(
    monkeypatch, auth_settings
):
    payload = _request()
    context = _context()
    receipt = AppointmentCreateResponse(
        id=uuid4(), status="UPCOMING", created_at=datetime(2026, 10, 1, tzinfo=UTC)
    )
    reads = 0

    async def lookup(*_args, **_kwargs):
        nonlocal reads
        reads += 1
        return None if reads == 1 else (receipt, True)

    async def named_duplicate(*_args, **_kwargs):
        raise booking._NamedDuplicate()

    monkeypatch.setattr(booking, "_read_committed_key", lookup)
    monkeypatch.setattr(booking, "_run_attempt_with_commit_safety", named_duplicate)
    result = await booking.create_appointment(
        CleanSession(),
        payload,
        context=context,
        idempotency_key="winner-key",
        request_id="request-3",
        settings=auth_settings,
        deadline=asyncio.get_running_loop().time() + 2,
    )
    assert result == (receipt, True)
    assert reads == 2


def test_mysql_duplicate_detection_requires_the_exact_named_constraint():
    exact = IntegrityError(
        "INSERT INTO appointments",
        {},
        Exception(1062, "Duplicate entry for key 'appointments.uq_appointments_creation_request'"),
    )
    unrelated = IntegrityError(
        "INSERT INTO appointments",
        {},
        Exception(1062, "Duplicate entry for key 'appointments.some_other_unique'"),
    )

    assert booking._is_named_creation_duplicate(exact)
    assert not booking._is_named_creation_duplicate(unrelated)


async def test_booking_service_stages_one_appointment_audit_and_two_notifications(
    monkeypatch, auth_settings
):
    payload = _request().model_copy(
        update={"starts_at": datetime(2026, 10, 16, 1, 0, tzinfo=UTC), "description": "  text  "}
    )
    context = _context()
    now = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
    fake_db = TransactionSession()
    fake_student = User(
        id=context.user_id,
        email="student@example.invalid",
        name="Student",
        role="STUDENT",
        password_hash="unused",
        is_active=True,
    )
    fake_staff = User(
        id=payload.staff_id,
        email="staff@example.invalid",
        name="Staff",
        role="STAFF",
        password_hash="unused",
        is_active=True,
    )
    setting = ServiceSetting(
        timezone="Asia/Ho_Chi_Minh", start_step_minutes=15, booking_horizon_days=7
    )
    purpose = Purpose(
        id=payload.purpose_id,
        code="TEST",
        name="Test purpose",
        duration_minutes=30,
        requires_free_text=False,
        is_active=True,
    )

    async def no_preexisting_key(*_args, **_kwargs):
        return None

    async def no_recheck_row(*_args, **_kwargs):
        return None

    async def lock_settings(_db):
        return setting

    async def lock_user(_db, user_id):
        return {context.user_id: fake_student, payload.staff_id: fake_staff}[user_id]

    async def read_purpose(_db, _purpose_id):
        return purpose

    async def accepting_staff(_db, _purpose_id, staff_id):
        return staff_id

    async def utc_now(_db):
        return now

    async def no_busy_time(_db, **_kwargs):
        return ()

    async def read_windows(_db, _weekday):
        return (ServiceWindow(opens_at=time(8), closes_at=time(10)),)

    async def count_quota(_db, *, student_id):
        assert student_id == context.user_id
        return 0

    async def stage_bundle(db, *, appointment, audit_event, notifications):
        db.added.extend((appointment, audit_event, *notifications))

    monkeypatch.setattr(booking, "_read_committed_key", no_preexisting_key)
    monkeypatch.setattr(booking.appointments, "find_creation_request", no_recheck_row)
    monkeypatch.setattr(booking.appointments, "lock_service_settings", lock_settings)
    monkeypatch.setattr(booking.appointments, "count_blocking_for_student", count_quota)
    monkeypatch.setattr(booking.appointments, "add_creation_bundle", stage_bundle)
    monkeypatch.setattr(booking.users, "lock_by_id", lock_user)
    monkeypatch.setattr(booking.users, "utc_now", utc_now)
    monkeypatch.setattr(booking.catalog, "find_active_purpose", read_purpose)
    monkeypatch.setattr(booking.catalog, "find_accepting_staff", accepting_staff)
    monkeypatch.setattr(booking.availability, "read_windows", read_windows)
    monkeypatch.setattr(booking.availability, "read_unavailability", no_busy_time)
    monkeypatch.setattr(booking.availability, "read_staff_appointments", no_busy_time)
    monkeypatch.setattr(booking.availability, "read_student_appointments", no_busy_time)

    result, replayed = await booking.create_appointment(
        fake_db,
        payload,
        context=context,
        idempotency_key="unit-key",
        request_id="request-unit",
        settings=auth_settings,
        deadline=asyncio.get_running_loop().time() + 2,
    )

    assert replayed is False
    assert result.status == "UPCOMING"
    assert result.created_at == datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
    appointment, audit, *notifications = fake_db.added
    assert isinstance(appointment, Appointment)
    assert appointment.id == result.id
    assert appointment.other_reason == "  text  "
    assert appointment.ends_at == datetime(2026, 10, 16, 1, 30, tzinfo=UTC)
    assert isinstance(audit, AuditEvent)
    assert audit.actor_id == context.user_id
    assert "text" not in repr(audit.event_metadata)
    assert len(notifications) == 2
    assert all(isinstance(row, Notification) for row in notifications)
    assert {row.recipient_id for row in notifications} == {context.user_id, payload.staff_id}
    assert all(row.appointment_id == result.id for row in notifications)
    assert fake_db.commits == 1
    assert fake_db.rollbacks == 0


async def test_uncertain_commit_invalidates_connection_and_is_never_retried(
    monkeypatch, auth_settings
):
    payload = _request()
    context = _context()
    lost_connection = OperationalError("COMMIT", {}, Exception(2013, "lost connection"))
    fake_db = TransactionSession(commit_error=lost_connection)
    setting = ServiceSetting(
        timezone="Asia/Ho_Chi_Minh", start_step_minutes=15, booking_horizon_days=7
    )
    purpose = Purpose(
        id=payload.purpose_id,
        code="TEST",
        name="Test purpose",
        duration_minutes=30,
        requires_free_text=False,
        is_active=True,
    )
    fake_student = User(
        id=context.user_id,
        email="student@example.invalid",
        name="Student",
        role="STUDENT",
        password_hash="unused",
        is_active=True,
    )
    fake_staff = User(
        id=payload.staff_id,
        email="staff@example.invalid",
        name="Staff",
        role="STAFF",
        password_hash="unused",
        is_active=True,
    )
    now = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)

    async def no_row(*_args, **_kwargs):
        return None

    async def lock_user(_db, user_id):
        return {context.user_id: fake_student, payload.staff_id: fake_staff}[user_id]

    async def accepting_staff(_db, _purpose_id, staff_id):
        return staff_id

    async def no_busy_time(*_args, **_kwargs):
        return ()

    async def read_windows(_db, _weekday):
        return (ServiceWindow(opens_at=time(8), closes_at=time(10)),)

    async def stage_bundle(db, *, appointment, audit_event, notifications):
        db.added.extend((appointment, audit_event, *notifications))

    monkeypatch.setattr(booking, "_read_committed_key", no_row)
    monkeypatch.setattr(
        booking.appointments, "lock_service_settings", lambda _db: _async_value(setting)
    )
    monkeypatch.setattr(booking.appointments, "find_creation_request", no_row)
    monkeypatch.setattr(
        booking.appointments, "count_blocking_for_student", lambda *_a, **_k: _async_value(0)
    )
    monkeypatch.setattr(booking.appointments, "add_creation_bundle", stage_bundle)
    monkeypatch.setattr(booking.users, "lock_by_id", lock_user)
    monkeypatch.setattr(booking.users, "utc_now", lambda _db: _async_value(now))
    monkeypatch.setattr(
        booking.catalog, "find_active_purpose", lambda *_a, **_k: _async_value(purpose)
    )
    monkeypatch.setattr(booking.catalog, "find_accepting_staff", accepting_staff)
    monkeypatch.setattr(booking.availability, "read_windows", read_windows)
    monkeypatch.setattr(booking.availability, "read_unavailability", no_busy_time)
    monkeypatch.setattr(booking.availability, "read_staff_appointments", no_busy_time)
    monkeypatch.setattr(booking.availability, "read_student_appointments", no_busy_time)

    attempts = 0
    run_attempt = booking._run_attempt_with_commit_safety

    async def count_attempt(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return await run_attempt(*args, **kwargs)

    monkeypatch.setattr(booking, "_run_attempt_with_commit_safety", count_attempt)
    with pytest.raises(AppError) as caught:
        await booking.create_appointment(
            fake_db,
            payload,
            context=context,
            idempotency_key="uncertain-commit-key",
            request_id="uncertain-commit-request",
            settings=auth_settings.model_copy(update={"max_deadlock_retries": 2}),
            deadline=asyncio.get_running_loop().time() + 2,
        )

    assert (caught.value.status_code, caught.value.code) == (503, "SERVICE_UNAVAILABLE")
    assert attempts == 1
    assert fake_db.commits == 1
    assert fake_db.invalidations == 1
    assert fake_db.rollbacks == 1


async def _async_value(value):
    return value
