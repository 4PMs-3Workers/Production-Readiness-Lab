from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.backend.integration.worker.helpers import WorkerEnv

from app.models import Purpose, ServiceHour, ServiceSetting, StaffPurpose, User
from app.repositories import users


@pytest_asyncio.fixture
async def worker_env(mysql_engine, auth_settings):
    """No demo seed, API lifespan, interactive session or password hashing dependency."""
    factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    student_id, staff_id, purpose_id = uuid4(), uuid4(), uuid4()
    async with factory.begin() as db:
        now = await users.utc_now(db)
        local_day = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date() + timedelta(days=1)
        start = datetime.combine(local_day, time(9), ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(UTC)
        db.add_all(
            [
                User(
                    id=student_id,
                    email="worker-student@example.invalid",
                    name="Worker Student",
                    role="STUDENT",
                    password_hash="unused-worker-fixture-hash",
                ),
                User(
                    id=staff_id,
                    email="worker-staff@example.invalid",
                    name="Worker Staff",
                    role="STAFF",
                    password_hash="unused-worker-fixture-hash",
                ),
                Purpose(
                    id=purpose_id, code="WORKER_TEST", name="Worker purpose", duration_minutes=30
                ),
                ServiceSetting(
                    timezone="Asia/Ho_Chi_Minh", start_step_minutes=15, booking_horizon_days=7
                ),
                ServiceHour(weekday=local_day.isoweekday(), opens_at=time(8), closes_at=time(17)),
            ]
        )
        await db.flush()
        db.add(StaffPurpose(staff_id=staff_id, purpose_id=purpose_id))
    settings = auth_settings.model_copy(
        update={"accepted_schema_revisions_raw": "20261019_1200_proposal_replay"}
    )
    return WorkerEnv(mysql_engine, factory, settings, student_id, staff_id, purpose_id, now, start)
