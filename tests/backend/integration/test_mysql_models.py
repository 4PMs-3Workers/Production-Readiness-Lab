from datetime import UTC, datetime, time, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AuditEvent,
    Base,
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

pytestmark = pytest.mark.mysql
START = (
    (datetime.now(UTC) + timedelta(days=30))
    .astimezone(timezone(timedelta(hours=7)))
    .replace(microsecond=123456)
)


def mysql_error_code(error: DBAPIError) -> int:
    original = error.orig
    assert original is not None
    code = original.args[0]
    assert isinstance(code, int)
    return code


@pytest_asyncio.fixture
async def graph(mysql_engine):
    async with AsyncSession(mysql_engine, expire_on_commit=False) as session:
        student = User(
            email="  STUDENT@Example.COM ", password_hash="fake", name="Sinh viên", role="STUDENT"
        )
        staff = User(email="staff@example.com", password_hash="fake", name="Cố vấn", role="STAFF")
        purpose = Purpose(code="ACADEMIC_ADVISOR", name="Tư vấn học tập", duration_minutes=30)
        settings = ServiceSetting()
        hours = ServiceHour(weekday=1, opens_at=time(8), closes_at=time(17))
        session.add_all([student, staff, purpose, settings, hours])
        await session.flush()
        acceptance = StaffPurpose(staff_id=staff.id, purpose_id=purpose.id)
        auth = Session(
            user_id=student.id, token_hash=b"t" * 32, expires_at=START + timedelta(days=365)
        )
        unavailable = StaffUnavailability(
            staff_id=staff.id,
            starts_at=START + timedelta(days=1),
            ends_at=START + timedelta(days=1, hours=1),
        )
        session.add_all([acceptance, auth, unavailable])
        await session.flush()
        appointment = Appointment(
            student_id=student.id,
            staff_id=staff.id,
            purpose_id=purpose.id,
            duration_minutes=30,
            starts_at=START,
            ends_at=START + timedelta(minutes=30),
            creation_request_key="booking",
            creation_request_hash=b"h" * 32,
        )
        session.add(appointment)
        await session.flush()
        assert appointment.status == "UPCOMING"
        proposal = ChangeProposal(
            appointment_id=appointment.id,
            created_by=staff.id,
            proposed_starts_at=START + timedelta(hours=1),
            proposed_ends_at=START + timedelta(hours=1, minutes=30),
            expires_at=START.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        notification = Notification(
            recipient_id=student.id, appointment_id=appointment.id, event_type="APPOINTMENT_CREATED"
        )
        audit = AuditEvent(
            action="APPOINTMENT_CREATED",
            entity_type="APPOINTMENT",
            entity_id=str(appointment.id),
            request_id="test-request",
        )
        appointment.status = "CHANGE_PENDING"
        session.add_all([proposal, notification, audit])
        await session.commit()
        yield session, {
            "users": student,
            "sessions": auth,
            "purposes": purpose,
            "staff_purposes": acceptance,
            "service_settings": settings,
            "service_hours": hours,
            "staff_unavailability": unavailable,
            "appointments": appointment,
            "change_proposals": proposal,
            "notifications": notification,
            "audit_events": audit,
        }


async def test_all_models_round_trip_with_server_defaults(graph):
    session, rows = graph
    # Server defaults must already be usable without implicit asynchronous IO.
    assert rows["appointments"].created_at.tzinfo is UTC
    assert rows["change_proposals"].pending_guard == 1
    session.expunge_all()
    for table, row in rows.items():
        model = type(row)
        if table == "staff_purposes":
            loaded = await session.get(model, (row.staff_id, row.purpose_id))
        else:
            loaded = await session.get(model, row.id)
        assert loaded is not None
        if hasattr(loaded, "created_at"):
            assert loaded.created_at.tzinfo is UTC
    appointment = await session.get(Appointment, rows["appointments"].id)
    assert appointment.starts_at == START.astimezone(UTC)
    assert appointment.starts_at.microsecond == 123456
    assert appointment.creation_request_hash == b"h" * 32
    assert appointment.status == "CHANGE_PENDING"
    assert appointment.other_reason is None
    settings = await session.get(ServiceSetting, 1)
    assert (settings.timezone, settings.start_step_minutes, settings.booking_horizon_days) == (
        "Asia/Ho_Chi_Minh",
        15,
        7,
    )
    assert (await session.get(ServiceHour, rows["service_hours"].id)).opens_at == time(8)
    assert (await session.get(User, rows["users"].id)).email == "student@example.com"
    assert (await session.get(AuditEvent, rows["audit_events"].id)).event_metadata == {}
    assert (await session.get(ChangeProposal, rows["change_proposals"].id)).pending_guard == 1
    raw = (
        await session.execute(
            text("SELECT HEX(id), starts_at FROM appointments WHERE id = :id"),
            {"id": appointment.id.bytes},
        )
    ).one()
    assert raw[0].lower() == appointment.id.hex
    assert raw[1] == START.astimezone(UTC).replace(tzinfo=None)
    tables = (await session.execute(text("SHOW TABLES"))).scalars().all()
    assert set(tables) == set(Base.metadata.tables)


async def test_connection_invariants_survive_new_connections(mysql_engine):
    for _ in range(2):
        async with mysql_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT @@session.time_zone, @@session.transaction_isolation, "
                        "@@session.sql_mode"
                    )
                )
            ).one()
            assert row[0] == "+00:00"
            assert row[1] == "READ-COMMITTED"
            assert {"STRICT_TRANS_TABLES", "NO_ZERO_DATE", "NO_ZERO_IN_DATE"} <= set(
                row[2].split(",")
            )


@pytest.mark.parametrize(
    "table, assignment",
    [
        ("users", "role = 'student'"),
        ("users", "email = '   '"),
        ("users", "email = 'UPPER@example.com'"),
        ("users", "name = ''"),
        ("users", "is_active = 2"),
        ("purposes", "duration_minutes = 0"),
        ("purposes", "code = ''"),
        ("purposes", "name = ''"),
        ("purposes", "requires_free_text = 2"),
        ("purposes", "is_active = 2"),
        ("staff_purposes", "is_accepting = 2"),
        ("service_settings", "id = 2"),
        ("service_settings", "start_step_minutes = 0"),
        ("service_settings", "booking_horizon_days = 0"),
        ("service_hours", "weekday = 8"),
        ("service_hours", "opens_at = '-01:00:00'"),
        ("service_hours", "closes_at = '24:00:00'"),
        ("service_hours", "closes_at = opens_at"),
        ("staff_unavailability", "ends_at = starts_at"),
        ("appointments", "status = 'INVALID'"),
        ("appointments", "duration_minutes = 0"),
        ("appointments", "ends_at = DATE_ADD(ends_at, INTERVAL 1 MINUTE)"),
        ("appointments", "creation_request_key = ' '"),
        ("appointments", "creation_request_hash = X'00'"),
        ("appointments", "status = 'CANCELLED'"),
        ("appointments", "status = 'COMPLETED'"),
        ("change_proposals", "status = 'INVALID'"),
        ("change_proposals", "proposed_ends_at = proposed_starts_at"),
        ("change_proposals", "expires_at = created_at"),
        ("change_proposals", "resolved_at = created_at"),
        ("sessions", "token_hash = X'00'"),
        ("sessions", "expires_at = created_at"),
    ],
)
async def test_database_rejects_invalid_data_even_when_orm_is_bypassed(graph, table, assignment):
    session, _ = graph
    with pytest.raises(OperationalError) as error:
        async with session.begin_nested():
            await session.execute(text(f"UPDATE `{table}` SET {assignment}"))
    assert mysql_error_code(error.value) == 3819


async def test_idempotency_key_is_unique_per_student_and_case_sensitive(graph):
    session, rows = graph
    original = rows["appointments"]
    duplicate = Appointment(
        student_id=original.student_id,
        staff_id=original.staff_id,
        purpose_id=original.purpose_id,
        duration_minutes=30,
        starts_at=START + timedelta(hours=2),
        ends_at=START + timedelta(hours=2, minutes=30),
        creation_request_key="booking",
        creation_request_hash=b"h" * 32,
    )
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            session.add(duplicate)
            await session.flush()
    assert mysql_error_code(error.value) == 1062
    # The ORM must not lowercase or trim a client key.
    for hours, key in enumerate(["Booking", "booking "], start=3):
        session.add(
            Appointment(
                student_id=original.student_id,
                staff_id=original.staff_id,
                purpose_id=original.purpose_id,
                duration_minutes=30,
                starts_at=START + timedelta(hours=hours),
                ends_at=START + timedelta(hours=hours, minutes=30),
                creation_request_key=key,
                creation_request_hash=b"h" * 32,
            )
        )
        await session.flush()
    keys = (await session.scalars(select(Appointment.creation_request_key))).all()
    assert set(keys) == {"booking", "Booking", "booking "}


async def test_pending_guard_blocks_second_pending_but_retains_multiple_resolved(graph):
    session, rows = graph
    original = rows["change_proposals"]

    def new_proposal(status="PENDING"):
        return ChangeProposal(
            appointment_id=original.appointment_id,
            created_by=original.created_by,
            proposed_starts_at=original.proposed_starts_at,
            proposed_ends_at=original.proposed_ends_at,
            expires_at=original.expires_at,
            status=status,
            resolved_at=datetime.now(UTC) if status != "PENDING" else None,
            resolved_by=original.created_by if status == "SUPERSEDED" else None,
        )

    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            session.add(new_proposal())
            await session.flush()
    assert mysql_error_code(error.value) == 1062
    original.status = "SUPERSEDED"
    original.resolved_at = datetime.now(UTC)
    original.resolved_by = original.created_by
    await session.flush()
    session.add_all([new_proposal("SUPERSEDED"), new_proposal()])
    await session.flush()
    assert await session.scalar(select(func.count()).select_from(ChangeProposal)) == 3
    guards = (await session.scalars(select(ChangeProposal.pending_guard))).all()
    assert guards.count(None) == 2 and guards.count(1) == 1


async def test_composite_foreign_key_requires_an_existing_staff_purpose_pair(graph):
    session, rows = graph
    other = Purpose(code="OTHER", name="Khác", duration_minutes=30)
    session.add(other)
    await session.flush()
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            await session.execute(
                text("UPDATE appointments SET purpose_id = :id"), {"id": other.id.bytes}
            )
    assert mysql_error_code(error.value) == 1452
    assert rows["appointments"].purpose_id != other.id


async def test_foreign_keys_restrict_deletion_and_reject_missing_users(graph):
    session, rows = graph
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": rows["users"].id.bytes}
            )
    assert mysql_error_code(error.value) == 1451
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            await session.execute(text("UPDATE sessions SET user_id = :id"), {"id": uuid4().bytes})
    assert mysql_error_code(error.value) == 1452


async def test_email_collation_rejects_case_duplicate_but_preserves_accents(graph):
    session, _ = graph
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            session.add(
                User(
                    email="STUDENT@example.com",
                    password_hash="fake",
                    name="Duplicate",
                    role="STUDENT",
                )
            )
            await session.flush()
    assert mysql_error_code(error.value) == 1062
    # Query the stored column directly: ORM normalization must not mask collation errors.
    matched_id = await session.scalar(
        text("SELECT id FROM users WHERE email = :email"), {"email": "STUDENT@example.com"}
    )
    assert matched_id is not None
    for email in ["tést@example.com", "test@example.com"]:
        session.add(User(email=email, password_hash="fake", name="Test", role="STUDENT"))
    await session.flush()


async def test_json_metadata_defaults_are_independent_and_null_is_rejected(graph):
    session, rows = graph
    other = AuditEvent(action="TEST", entity_type="SETTINGS", entity_id="1", request_id="other")
    session.add(other)
    await session.flush()
    assert other.event_metadata is not rows["audit_events"].event_metadata
    with pytest.raises(IntegrityError) as error:
        async with session.begin_nested():
            await session.execute(text("UPDATE audit_events SET metadata = NULL"))
    assert mysql_error_code(error.value) == 1048


async def test_updated_at_is_written_by_backend_on_update(graph):
    session, rows = graph
    user = rows["users"]
    user.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    await session.flush()
    user.name = "Updated"
    await session.flush()
    assert user.updated_at > datetime(2020, 1, 1, tzinfo=UTC)
    assert user.updated_at.tzinfo is UTC


@pytest.mark.parametrize("value", ["0000-00-00 00:00:00", "2026-02-30 09:00:00"])
async def test_strict_sql_mode_rejects_invalid_dates(graph, value):
    session, _ = graph
    with pytest.raises(DBAPIError) as error:
        async with session.begin_nested():
            await session.execute(
                text("UPDATE staff_unavailability SET starts_at = :value"), {"value": value}
            )
    assert mysql_error_code(error.value) == 1292
