"""Integration tests create a unique schema and delete only that schema.

Schema initialization uses ``alembic upgrade head`` instead of
``Base.metadata.create_all()``.  This ensures the integration tests exercise
the migration path on every run, not just the ORM metadata snapshot.

Safety boundaries (unchanged from the original design):
- A uniquely named schema is created per test session; only that schema is
  dropped in the finally block, even when migration setup fails.
- The admin connection (database=None) creates and drops the schema;
  the test engine connects only to the isolated schema.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import create_database_engine
from app.core.passwords import PasswordPolicy, PasswordService
from app.core.schema_lifecycle import migration_connection
from app.factory import create_app
from app.models import Purpose, ServiceHour, ServiceSetting, StaffPurpose, User
from app.repositories import users


def _run_alembic_upgrade(connection: Connection) -> None:
    """Run ``alembic upgrade head`` using an already-open synchronous connection.

    The env.py detects ``config.attributes["connection"]`` and uses it directly,
    so migrations run inside the test schema's connection context.
    """
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def mysql_engine(request):
    url = os.getenv("MYSQL_TEST_URL")
    if not url:
        if request.config.getoption("--require-mysql"):
            pytest.fail("MYSQL_TEST_URL is required for backend acceptance checks")
        pytest.skip("Set MYSQL_TEST_URL to run tests against MySQL 8.4")
    schema = "prl_models_test_" + uuid4().hex
    admin = create_database_engine(
        make_url(url).set(database=None), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    engine = None
    created = False
    try:
        async with admin.begin() as connection:
            version = (await connection.execute(text("SELECT VERSION()"))).scalar_one()
            assert version.startswith("8.4."), f"Expected MySQL 8.4, got {version}"
            await connection.execute(
                text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            )
            created = True
        engine = create_database_engine(
            make_url(url).set(database=schema),
            poolclass=NullPool,
            connect_args={"connect_timeout": 5},
        )
        # Replace Base.metadata.create_all() with alembic upgrade head.
        # This exercises the full migration path on every integration test run.
        async with migration_connection(engine) as async_conn:
            await async_conn.run_sync(_run_alembic_upgrade)
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP DATABASE `{schema}`"))
        await admin.dispose()


@pytest_asyncio.fixture
async def auth_env(mysql_engine, auth_settings, request):
    updates: dict[str, Any] = {"login_ip_attempts": 100, "login_email_attempts": 100}
    if request.node.get_closest_marker("booking_stress") is not None:
        updates.update(
            {
                "request_timeout_seconds": 20.0,
                "db_query_timeout_seconds": 10.0,
                "db_lock_timeout_seconds": 5.0,
            }
        )
    settings = auth_settings.model_copy(update=updates)
    passwords = await PasswordService.create(PasswordPolicy(), 2)
    factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    async with factory.begin() as db:
        user = User(
            email="student@example.invalid",
            name="Test Student",
            role="STUDENT",
            password_hash=await passwords.hash("  Good-password!  "),
            is_active=True,
        )
        db.add(user)
    app = create_app(settings, engine_factory=lambda *a, **kw: mysql_engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        ) as client:
            yield client, factory, user.id, app


@dataclass(frozen=True)
class AvailabilityTestEnv:
    client: httpx.AsyncClient
    factory: async_sessionmaker[AsyncSession]
    app: Any
    student_id: UUID
    other_student_id: UUID
    staff_id: UUID
    other_staff_id: UUID
    purpose_id: UUID
    other_purpose_id: UUID


@pytest_asyncio.fixture
async def availability_env(auth_env):
    """Create the two-student/two-staff graph used by availability tests."""
    client, factory, student_id, app = auth_env
    other_student_id = uuid4()
    staff_id = uuid4()
    other_staff_id = uuid4()
    purpose_id = uuid4()
    other_purpose_id = uuid4()
    async with factory.begin() as db:
        student = await db.get(User, student_id)
        assert student is not None
        db.add_all(
            [
                User(
                    id=other_student_id,
                    email="other-student@example.invalid",
                    name="Other Student",
                    role="STUDENT",
                    password_hash=student.password_hash,
                ),
                User(
                    id=staff_id,
                    email="staff@example.invalid",
                    name="Selected Staff",
                    role="STAFF",
                    password_hash="unused-test-hash",
                ),
                User(
                    id=other_staff_id,
                    email="other-staff@example.invalid",
                    name="Other Staff",
                    role="STAFF",
                    password_hash="unused-test-hash",
                ),
                Purpose(
                    id=purpose_id,
                    code="SELECTED",
                    name="Selected purpose",
                    duration_minutes=30,
                ),
                Purpose(
                    id=other_purpose_id,
                    code="OTHER",
                    name="Other purpose",
                    duration_minutes=30,
                ),
                ServiceSetting(
                    timezone="Asia/Ho_Chi_Minh",
                    start_step_minutes=15,
                    booking_horizon_days=7,
                ),
                ServiceHour(weekday=5, opens_at=time(8, 10), closes_at=time(9, 10)),
            ]
        )
        await db.flush()
        db.add_all(
            [
                StaffPurpose(staff_id=staff_id, purpose_id=purpose_id),
                StaffPurpose(staff_id=staff_id, purpose_id=other_purpose_id),
                StaffPurpose(staff_id=other_staff_id, purpose_id=purpose_id),
                StaffPurpose(staff_id=other_staff_id, purpose_id=other_purpose_id),
            ]
        )
    return AvailabilityTestEnv(
        client=client,
        factory=factory,
        app=app,
        student_id=student_id,
        other_student_id=other_student_id,
        staff_id=staff_id,
        other_staff_id=other_staff_id,
        purpose_id=purpose_id,
        other_purpose_id=other_purpose_id,
    )


@pytest_asyncio.fixture
async def booking_env(availability_env):
    """Seed one current service day and return a valid upcoming appointment slot."""
    env = availability_env
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    async with env.factory() as db:
        now = await users.utc_now(db)
    selected_date = now.astimezone(zone).date() + timedelta(days=1)
    async with env.factory.begin() as db:
        await db.execute(delete(ServiceHour))
        db.add(
            ServiceHour(
                weekday=selected_date.isoweekday(),
                opens_at=time(8, 0),
                closes_at=time(10, 0),
            )
        )
        setting = await db.get(ServiceSetting, 1)
        assert setting is not None
        setting.timezone = "Asia/Ho_Chi_Minh"
        setting.start_step_minutes = 15
        setting.booking_horizon_days = 7
        purpose = await db.get(Purpose, env.purpose_id)
        assert purpose is not None
        purpose.duration_minutes = 30
    starts_at = datetime.combine(selected_date, time(8, 0), zone).astimezone(UTC)
    return env, selected_date, starts_at
