"""Deterministic fictional fixture generation and explicitly owned schema lifecycle."""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from tools.load.config import (
    LoadConfig,
    LoadSafetyError,
    server_level_database_url,
    validate_load_config,
)

from app.core.passwords import PasswordPolicy, PasswordService
from app.core.schema_lifecycle import migration_connection
from app.models import (
    Appointment,
    ChangeProposal,
    Purpose,
    ServiceHour,
    ServiceSetting,
    StaffPurpose,
    User,
)

_NAMESPACE = UUID("3b91f2e0-6d8f-4ba4-96c0-4055a98f8053")
_SCHEMA_NAME = re.compile(r"prl_load_([0-9a-f]{32})\Z")


@dataclass(frozen=True)
class FixtureRows:
    purpose: Purpose
    staff: list[User]
    students: list[User]
    assignments: list[StaffPurpose]
    appointments: list[Appointment]
    proposals: list[ChangeProposal]
    settings: ServiceSetting
    hours: list[ServiceHour]


def make_fixture_rows(
    *,
    seed: int,
    generated_at: datetime,
    password_hash: str,
    appointment_count: int = 1_000,
    run_id: str = "0" * 32,
) -> FixtureRows:
    """Build a stable 10-staff/40-student dataset without reading real user data."""
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    if appointment_count < 1_000 or appointment_count > 10_000:
        raise ValueError("appointment_count must be between 1000 and 10000")
    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise ValueError("run_id must be a lowercase UUID hex string")
    now = generated_at.astimezone(UTC)

    def identifier(kind: str, index: int = 0) -> UUID:
        return uuid5(_NAMESPACE, f"{seed}:{kind}:{index}")

    staff = [
        User(
            id=identifier("staff", index),
            email=f"load-{run_id[-8:]}-staff-{index:02d}@load.example.invalid",
            name=f"Load Staff {index:02d}",
            role="STAFF",
            password_hash=password_hash,
            is_active=True,
        )
        for index in range(10)
    ]
    students = [
        User(
            id=identifier("student", index),
            email=f"load-{run_id[-8:]}-student-{index:03d}@load.example.invalid",
            name=f"Load Student {index:03d}",
            role="STUDENT",
            password_hash=password_hash,
            is_active=True,
            student_code=f"LOAD{index:06d}",
        )
        for index in range(40)
    ]
    purpose = Purpose(
        id=identifier("purpose"),
        code="LOAD_TEST",
        name="Fictional load test",
        duration_minutes=30,
        requires_free_text=False,
        is_active=True,
    )
    assignments = [
        StaffPurpose(staff_id=row.id, purpose_id=purpose.id, is_accepting=True) for row in staff
    ]
    appointments: list[Appointment] = []
    proposals: list[ChangeProposal] = []
    for index in range(appointment_count):
        staff_index = index % len(staff)
        student_index = index % len(students)
        active_proposal = index < 10
        active_booking = 10 <= index < 20
        if active_proposal or active_booking:
            starts_at = now + timedelta(
                days=5 if active_proposal else 6,
                minutes=(index % 10) * 15,
            )
            status = "CHANGE_PENDING" if active_proposal else "UPCOMING"
            created_at = now - timedelta(days=1)
            outcome_at = None
            outcome_by = None
            cancelled_at = None
            cancelled_by = None
        else:
            starts_at = (now - timedelta(days=2 + index)).replace(hour=10, minute=0, second=0)
            status = "CANCELLED" if index % 10 == 0 else "COMPLETED"
            created_at = starts_at - timedelta(days=5)
            outcome_at = starts_at + timedelta(minutes=30) if status == "COMPLETED" else None
            outcome_by = staff[staff_index].id if status == "COMPLETED" else None
            cancelled_at = starts_at - timedelta(days=1) if status == "CANCELLED" else None
            cancelled_by = students[student_index].id if status == "CANCELLED" else None
        appointment = Appointment(
            id=identifier("appointment", index),
            student_id=students[student_index].id,
            staff_id=staff[staff_index].id,
            purpose_id=purpose.id,
            duration_minutes=30,
            other_reason=None,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            location=f"Fictional room {staff_index + 1:02d}",
            status=status,
            creation_request_key=f"load-fixture-{seed}-{index}",
            creation_request_hash=hashlib.sha256(f"{seed}:{index}".encode()).digest(),
            proposal_version=1 if active_proposal else 0,
            cancelled_at=cancelled_at,
            cancelled_by=cancelled_by,
            cancel_reason="Fictional fixture cancellation" if cancelled_at else None,
            outcome_at=outcome_at,
            outcome_by=outcome_by,
            created_at=created_at,
        )
        appointments.append(appointment)
        if active_proposal:
            proposals.append(
                ChangeProposal(
                    id=identifier("proposal", index),
                    appointment_id=appointment.id,
                    created_by=staff[staff_index].id,
                    creation_request_key=f"load-proposal-{seed}-{index}",
                    creation_request_hash=hashlib.sha256(
                        f"proposal:{seed}:{index}".encode()
                    ).digest(),
                    created_version=1,
                    proposed_starts_at=starts_at + timedelta(days=1),
                    proposed_ends_at=starts_at + timedelta(days=1, minutes=30),
                    proposed_location=appointment.location,
                    status="PENDING",
                    expires_at=starts_at - timedelta(days=2),
                    created_at=created_at,
                    resolved_at=None,
                    resolved_by=None,
                )
            )
    settings = ServiceSetting(
        id=1,
        timezone="Asia/Ho_Chi_Minh",
        start_step_minutes=15,
        booking_horizon_days=7,
        updated_at=now,
    )
    hours = [
        ServiceHour(
            id=identifier("service-hour", weekday),
            weekday=weekday,
            opens_at=time(8, 0),
            closes_at=time(17, 0),
        )
        for weekday in range(1, 8)
    ]
    return FixtureRows(
        purpose, staff, students, assignments, appointments, proposals, settings, hours
    )


def _upgrade_schema(connection) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def validate_cleanup_ownership(schema_name: str, run_id: str, persisted_run_id: str) -> None:
    """Prove cleanup targets exactly the prefixed schema created for this run."""
    match = _SCHEMA_NAME.fullmatch(schema_name)
    if match is None or match.group(1) != run_id or persisted_run_id != run_id:
        raise LoadSafetyError("Schema ownership marker does not match; refusing cleanup")


async def create_owned_schema(config: LoadConfig, *, seed: int, password: str) -> str:
    """Create and seed a new prefixed schema, returning its run ownership token."""
    validate_load_config(config)
    if len(password) < 32:
        raise LoadSafetyError("Fixture password must be a private per-run secret of 32+ chars")
    match = _SCHEMA_NAME.fullmatch(config.schema_name)
    if match is None:
        raise LoadSafetyError("Refusing to create a schema outside the load namespace")
    run_id = match.group(1)
    admin_url = server_level_database_url(config.database_url)
    admin = create_async_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    schema_created = False
    engine: AsyncEngine | None = None
    try:
        async with admin.begin() as connection:
            if await connection.scalar(text("SELECT DATABASE()")) is not None:
                raise LoadSafetyError("Admin connection unexpectedly selected a database")
            existing = await connection.execute(
                text(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
                ),
                {"name": config.schema_name},
            )
            if existing.scalar_one_or_none() is not None:
                raise LoadSafetyError("Dedicated load schema already exists; refusing reuse")
            create_task = asyncio.create_task(
                connection.execute(
                    text(
                        f"CREATE DATABASE `{config.schema_name}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                    )
                )
            )
            try:
                await asyncio.shield(create_task)
            except asyncio.CancelledError:
                # DDL may commit server-side even if cancellation arrives before the
                # client receives its result. Await the in-flight result so rollback
                # knows whether this invocation owns a schema to remove.
                try:
                    await create_task
                except Exception:
                    raise
                schema_created = True
                raise
            else:
                schema_created = True
        engine = create_async_engine(
            make_url(config.database_url),
            poolclass=NullPool,
            connect_args={"connect_timeout": 5},
        )
        async with migration_connection(engine) as connection:
            if await connection.scalar(text("SELECT DATABASE()")) != config.schema_name:
                raise LoadSafetyError("Migration connection resolved to an unexpected schema")
            await connection.run_sync(_upgrade_schema)
        password_service = await PasswordService.create(PasswordPolicy(), 2)
        password_hash = await password_service.hash(password)
        rows = make_fixture_rows(
            seed=seed,
            generated_at=datetime.now(UTC),
            password_hash=password_hash,
            run_id=run_id,
        )
        async with engine.begin() as connection:
            if await connection.scalar(text("SELECT DATABASE()")) != config.schema_name:
                raise LoadSafetyError("Fixture connection resolved to an unexpected schema")
            await connection.execute(
                text(
                    "CREATE TABLE prl_load_ownership ("
                    "run_id CHAR(32) NOT NULL PRIMARY KEY, source_sha CHAR(40) NOT NULL, "
                    "fixture_count INT NOT NULL, created_at DATETIME(6) NOT NULL) "
                    "ENGINE=InnoDB"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO prl_load_ownership "
                    "(run_id, source_sha, fixture_count, created_at) "
                    "VALUES (:run_id, :source_sha, :fixture_count, :created_at)"
                ),
                {
                    "run_id": run_id,
                    "source_sha": config.release_sha,
                    "fixture_count": len(rows.appointments),
                    "created_at": datetime.now(UTC),
                },
            )
            for collection in (
                [rows.purpose],
                rows.staff,
                rows.students,
                rows.assignments,
                [rows.settings],
                rows.hours,
                rows.appointments,
                rows.proposals,
            ):
                if collection:
                    await connection.run_sync(
                        lambda sync_conn, values=collection: _bulk_add(sync_conn, values)
                    )
        return run_id
    except BaseException:
        if engine is not None:
            await engine.dispose()
        if schema_created:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP DATABASE `{config.schema_name}`"))
        raise
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.dispose()


async def verify_owned_schema(config: LoadConfig, *, run_id: str) -> dict[str, int]:
    """Confirm the configured application URL resolves to this run's seeded schema."""
    validate_load_config(config)
    match = _SCHEMA_NAME.fullmatch(config.schema_name)
    if match is None or match.group(1) != run_id:
        raise LoadSafetyError("Run ID does not match the configured load schema")
    engine = create_async_engine(
        make_url(config.database_url), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        async with engine.connect() as connection:
            marker = await connection.execute(
                text(
                    "SELECT source_sha, fixture_count FROM prl_load_ownership "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            ownership = marker.one_or_none()
            if ownership is None or ownership[0] != config.release_sha:
                raise LoadSafetyError("Fixture ownership or release marker does not match")
            staff_count = await connection.scalar(
                text("SELECT COUNT(*) FROM users WHERE role = 'STAFF' AND email LIKE 'load-%'")
            )
            appointment_count = await connection.scalar(text("SELECT COUNT(*) FROM appointments"))
            if (
                int(staff_count or 0) < 10
                or int(appointment_count or 0) != int(ownership[1])
                or int(appointment_count or 0) < 1_000
            ):
                raise LoadSafetyError("Owned load fixture is incomplete")
            return {"staff": int(staff_count), "appointments": int(appointment_count)}
    finally:
        await engine.dispose()


def _bulk_add(connection, objects: list[object]) -> None:
    from sqlalchemy.orm import Session

    with Session(connection, join_transaction_mode="create_savepoint") as session:
        session.add_all(objects)
        session.flush()
        session.commit()


async def drop_owned_schema(config: LoadConfig, *, run_id: str) -> None:
    """Drop exactly one run schema, after checking its schema and ownership row."""
    validate_load_config(config)
    match = _SCHEMA_NAME.fullmatch(config.schema_name)
    if match is None or match.group(1) != run_id:
        raise LoadSafetyError("Cleanup run ID does not match the schema name")
    admin_url = server_level_database_url(config.database_url)
    admin = create_async_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        async with admin.begin() as connection:
            if await connection.scalar(text("SELECT DATABASE()")) is not None:
                raise LoadSafetyError("Admin connection unexpectedly selected a database")
            exists = await connection.execute(
                text(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :name"
                ),
                {"name": config.schema_name},
            )
            if exists.scalar_one_or_none() is None:
                raise LoadSafetyError("Owned load schema does not exist")
            ownership = await connection.execute(
                text(
                    f"SELECT run_id FROM `{config.schema_name}`.prl_load_ownership "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            validate_cleanup_ownership(
                config.schema_name, run_id, ownership.scalar_one_or_none() or ""
            )
            await connection.execute(text(f"DROP DATABASE `{config.schema_name}`"))
    finally:
        await admin.dispose()
