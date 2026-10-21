"""Staff business-time context uses the shared, authoritative calendar."""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from tests.backend.integration.test_booking_context import assert_private

from app.dependencies.clock import get_clock
from app.dependencies.database import get_db_session
from app.models import ServiceSetting, User
from app.repositories import availability

PATH = "/api/v1/staff/me/context"
ORIGIN = "http://localhost:3000"


async def sign_in_staff(env):
    async with env.factory.begin() as db:
        student = await db.get(User, env.student_id)
        staff = await db.get(User, env.staff_id)
        staff.password_hash = student.password_hash
    response = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "staff@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "timezone,instant,expected",
    [
        ("Asia/Ho_Chi_Minh", "2026-10-15T17:00:00+00:00", "2026-10-16"),
        ("America/Los_Angeles", "2026-01-01T01:00:00+00:00", "2025-12-31"),
    ],
)
async def test_staff_context_uses_database_timezone_and_one_clock_sample(
    availability_env, timezone, instant, expected
):
    env = availability_env
    await sign_in_staff(env)
    env.client.cookies.delete("prl_csrf")
    async with env.factory.begin() as db:
        setting = await db.get(ServiceSetting, 1)
        setting.timezone = timezone
    samples = []

    def clock():
        samples.append(instant)
        assert len(samples) == 1
        return datetime.fromisoformat(instant)

    env.app.dependency_overrides[get_clock] = lambda: clock
    response = await env.client.get(PATH, headers={"Date": "Thu, 01 Jan 2099 00:00:00 GMT"})
    assert_private(response)
    assert response.json() == {"timezone": timezone, "business_date": expected}
    assert len(samples) == 1


@pytest.mark.parametrize("role", [None, "STUDENT", "ADMIN"])
async def test_staff_context_requires_staff_role(availability_env, role):
    env = availability_env
    if role:
        await sign_in_staff(env)
        async with env.factory.begin() as db:
            staff = await db.get(User, env.staff_id)
            staff.role = role
    assert_private(
        await env.client.get(PATH),
        status=403 if role else 401,
        code="FORBIDDEN_ROLE" if role else "INVALID_SESSION",
    )


@pytest.mark.parametrize("query", ["date=2099-01-01", "timezone=UTC", "x=1&x=2"])
async def test_staff_context_rejects_queries(availability_env, query):
    env = availability_env
    await sign_in_staff(env)
    assert_private(await env.client.get(PATH + "?" + query), status=422, code="VALIDATION_ERROR")


@pytest.mark.parametrize("missing", [True, False])
async def test_staff_context_missing_or_invalid_settings_fail_closed(availability_env, missing):
    env = availability_env
    await sign_in_staff(env)
    async with env.factory.begin() as db:
        setting = await db.get(ServiceSetting, 1)
        if missing:
            await db.delete(setting)
        else:
            setting.timezone = "secret-invalid-zone"
    response = await env.client.get(PATH)
    assert_private(response, status=503, code="SERVICE_UNAVAILABLE")
    assert "secret-invalid-zone" not in response.text
    assert "settings" not in response.text.lower()


@pytest.mark.parametrize("failure", ["deadline", "unexpected", "database"])
async def test_staff_context_failures_are_sanitized(availability_env, monkeypatch, caplog, failure):
    env = availability_env
    await sign_in_staff(env)
    if failure == "deadline":
        env.app.state.settings = env.app.state.settings.model_copy(
            update={"db_query_timeout_seconds": 0.2}
        )

    async def fail(db):
        if failure == "deadline":
            await asyncio.sleep(10)
        if failure == "database":
            raise OperationalError("secret-sql", {}, Exception("secret-password"))
        raise RuntimeError("secret-staff-data")

    monkeypatch.setattr(availability, "read_settings", fail)
    response = await env.client.get(PATH)
    status = 500 if failure == "unexpected" else 503
    assert_private(
        response,
        status=status,
        code="INTERNAL_SERVER_ERROR" if status == 500 else "SERVICE_UNAVAILABLE",
    )
    assert "secret-" not in response.text + caplog.text


async def test_staff_context_database_timeout_releases_read_transaction(
    availability_env, monkeypatch
):
    env = availability_env
    await sign_in_staff(env)
    env.app.state.settings = env.app.state.settings.model_copy(
        update={"db_query_timeout_seconds": 0.2}
    )
    original = availability.read_settings

    async def delayed_read(db):
        await db.execute(text("SELECT SLEEP(0.6)"))
        return await original(db)

    async with env.factory() as db:

        async def held_session():
            yield db

        env.app.dependency_overrides[get_db_session] = held_session
        try:
            with monkeypatch.context() as patch:
                patch.setattr(availability, "read_settings", delayed_read)
                response = await env.client.get(PATH)
            assert_private(response, status=503, code="SERVICE_UNAVAILABLE")
            assert not db.in_transaction()
            assert await db.scalar(text("SELECT 1")) == 1
            await db.rollback()
            recovered = await env.client.get(PATH)
            assert_private(recovered)
            assert not db.in_transaction()
        finally:
            env.app.dependency_overrides.pop(get_db_session)
