"""Booking context HTTP contract with real settings and session cookies."""

import asyncio
from datetime import datetime

import httpx
import pytest

from app.dependencies.clock import get_clock
from app.models import ServiceSetting, User

PATH = "/api/v1/booking-context"


async def sign_in(env):
    response = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200


def assert_private(response, *, status=200, code=None):
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"]
    if code:
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["X-Error-Class"] == ("service" if status >= 500 else "input")


@pytest.mark.parametrize(
    "timezone,horizon,instant,expected",
    [
        (
            "Asia/Ho_Chi_Minh",
            7,
            "2026-10-15T17:00:00+00:00",
            ("2026-10-16", "2026-10-17", "2026-10-23"),
        ),
        (
            "America/Los_Angeles",
            3,
            "2026-01-01T01:00:00+00:00",
            ("2025-12-31", "2026-01-01", "2026-01-03"),
        ),
    ],
)
async def test_context_uses_one_server_clock_sample_and_database_policy_without_csrf(
    availability_env, timezone, horizon, instant, expected
):
    env = availability_env
    await sign_in(env)
    env.client.cookies.delete("prl_csrf")
    async with env.factory.begin() as db:
        setting = await db.get(ServiceSetting, 1)
        setting.timezone = timezone
        setting.booking_horizon_days = horizon
    samples = []

    def clock():
        samples.append(instant)
        assert len(samples) == 1
        return datetime.fromisoformat(instant)

    env.app.dependency_overrides[get_clock] = lambda: clock
    response = await env.client.get(PATH, headers={"Date": "Thu, 01 Jan 2099 00:00:00 GMT"})
    assert_private(response)
    assert response.json() == dict(
        timezone=timezone,
        business_date=expected[0],
        min_booking_date=expected[1],
        max_booking_date=expected[2],
    )
    assert len(samples) == 1


@pytest.mark.parametrize("query", ["date=2099-01-01", "timezone=UTC", "x=1&x=2"])
async def test_context_rejects_all_extra_and_duplicate_query_parameters(availability_env, query):
    env = availability_env
    await sign_in(env)
    assert_private(await env.client.get(PATH + "?" + query), status=422, code="VALIDATION_ERROR")


@pytest.mark.parametrize("role", [None, "STAFF", "ADMIN"])
async def test_context_requires_authenticated_student(availability_env, role):
    env = availability_env
    if role:
        await sign_in(env)
        async with env.factory.begin() as db:
            user = await db.get(User, env.student_id)
            user.role = role
    assert_private(
        await env.client.get(PATH),
        status=403 if role else 401,
        code="FORBIDDEN_ROLE" if role else "INVALID_SESSION",
    )


@pytest.mark.parametrize("missing", [True, False])
async def test_context_missing_or_invalid_db_settings_is_sanitized_503(availability_env, missing):
    env = availability_env
    await sign_in(env)
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


@pytest.mark.parametrize("prefix", ["", "/gateway"])
@pytest.mark.parametrize("failure", ["deadline", "request-deadline", "unexpected", "database"])
async def test_context_failures_use_private_sanitized_policy(
    availability_env, monkeypatch, prefix, failure, caplog
):
    from sqlalchemy.exc import OperationalError

    from app.repositories import availability

    env = availability_env
    await sign_in(env)
    if failure in {"deadline", "request-deadline"}:
        env.app.state.settings = env.app.state.settings.model_copy(
            update={"db_query_timeout_seconds": 10 if failure == "request-deadline" else 0.2}
        )

    async def fail(db):
        if failure in {"deadline", "request-deadline"}:
            await asyncio.sleep(10)
        elif failure == "database":
            raise OperationalError("secret-sql", {}, Exception("secret-password"))
        else:
            raise RuntimeError("secret-student-data")

    monkeypatch.setattr(availability, "read_settings", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, root_path=prefix),
        base_url="http://localhost:3000",
        cookies={"prl_session": env.client.cookies["prl_session"]},
    ) as client:
        started = asyncio.get_running_loop().time()
        response = await client.get(prefix + PATH)
        elapsed = asyncio.get_running_loop().time() - started
    if failure == "request-deadline":
        # The 3-second HTTP deadline must win over the 10-second DB timeout.
        assert elapsed < 5
    status = 500 if failure == "unexpected" else 503
    assert_private(
        response,
        status=status,
        code="INTERNAL_SERVER_ERROR" if status == 500 else "SERVICE_UNAVAILABLE",
    )
    assert "secret-" not in response.text + caplog.text
