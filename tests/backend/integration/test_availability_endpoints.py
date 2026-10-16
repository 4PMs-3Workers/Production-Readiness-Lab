"""Student availability HTTP contract against an Alembic-created MySQL schema."""

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.errors import ServiceUnavailable
from app.dependencies.catalog import require_student
from app.dependencies.clock import get_clock
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.factory import create_app
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    ServiceSetting,
    Session,
    StaffPurpose,
    User,
)
from app.schemas.auth import Role
from app.schemas.availability import AvailabilityResponse, AvailableSlot
from app.schemas.common import UtcTimestamp
from app.services import availability as availability_service

DAY = "2026-10-16"
NOW = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
ORIGIN = {"Origin": "http://localhost:3000"}
STUDENT_CREDENTIALS = {"email": "student@example.invalid", "password": "  Good-password!  "}
OTHER_STUDENT_CREDENTIALS = {
    "email": "other-student@example.invalid",
    "password": "  Good-password!  ",
}


def freeze_clock(app):
    from app.dependencies.clock import get_clock

    app.dependency_overrides[get_clock] = lambda: lambda: NOW


async def sign_in(client, credentials=STUDENT_CREDENTIALS):
    return await client.post("/api/v1/auth/login", json=credentials, headers=ORIGIN)


def availability_url(env, *, purpose_id=None, staff_id=None, day=DAY):
    return (
        "/api/v1/availability?"
        f"purpose_id={purpose_id or env.purpose_id}&"
        f"staff_id={staff_id or env.staff_id}&date={day}"
    )


def assert_private_response(response, *, error_class=None):
    assert response.headers["Cache-Control"] == "private, no-store"
    request_id = response.headers["X-Request-ID"]
    assert request_id
    if error_class is not None:
        assert response.headers["X-Error-Class"] == error_class
        assert response.json()["error"]["request_id"] == request_id


async def _add_appointment(env, *, student_id, staff_id, purpose_id, starts_at):
    appointment = Appointment(
        student_id=student_id,
        staff_id=staff_id,
        purpose_id=purpose_id,
        duration_minutes=30,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status="UPCOMING",
        creation_request_key=uuid4().hex,
        creation_request_hash=sha256(uuid4().bytes).digest(),
    )
    async with env.factory.begin() as db:
        db.add(appointment)
        await db.flush()


async def _rows(factory, model):
    async with factory() as db:
        result = await db.execute(select(*model.__table__.columns))
        return tuple(sorted((tuple(row) for row in result), key=repr))


def _student_context(role: Role = "STUDENT") -> AuthContext:
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="test@example.invalid",
        name="Test Student",
        role=role,
        expires_at=NOW + timedelta(hours=1),
    )


async def _fake_database():
    yield object()


def test_clock_dependency_returns_the_utc_clock_callable_without_sampling():
    from app.models.base import utc_now

    assert get_clock() is utc_now


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_route_uses_authenticated_identity_and_injected_clock_under_root_path(
    auth_settings, monkeypatch, prefix
):
    app = create_app(auth_settings)
    context = _student_context()
    app.dependency_overrides[require_student] = lambda: context
    app.dependency_overrides[get_db_session] = _fake_database
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    result = AvailabilityResponse(
        date=date.fromisoformat(DAY),
        timezone="Asia/Ho_Chi_Minh",
        slots=[
            AvailableSlot(
                starts_at=UtcTimestamp(datetime(2026, 10, 16, 1, 10, 0, 123456, tzinfo=UTC)),
                ends_at=UtcTimestamp(datetime(2026, 10, 16, 1, 40, 0, 123456, tzinfo=UTC)),
            )
        ],
    )
    received = []

    async def fake_service(db, query, *, student_id, settings, clock):
        received.append((db, query, student_id, settings, clock()))
        return result

    monkeypatch.setattr(availability_service, "get_availability", fake_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        response = await client.get(
            prefix + "/api/v1/availability?purpose_id=00000000-0000-0000-0000-000000000001"
            "&staff_id=00000000-0000-0000-0000-000000000002&date=2026-10-16"
        )

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert_private_response(response)
    assert len(received) == 1
    assert received[0][2] == context.user_id
    assert received[0][4] == NOW


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_route_unexpected_error_uses_private_outer_fallback(
    auth_settings, monkeypatch, caplog, prefix
):
    import logging

    app = create_app(auth_settings)
    app.dependency_overrides[require_student] = lambda: _student_context()
    app.dependency_overrides[get_db_session] = _fake_database
    app.dependency_overrides[get_clock] = lambda: lambda: NOW

    async def explode(*args, **kwargs):
        raise RuntimeError("synthetic-secret-sql")

    monkeypatch.setattr(availability_service, "get_availability", explode)
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path=prefix),
            base_url="http://localhost:3000",
        ) as client:
            response = await client.get(
                prefix + "/api/v1/availability?purpose_id=00000000-0000-0000-0000-000000000001"
                "&staff_id=00000000-0000-0000-0000-000000000002&date=2026-10-16"
            )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert_private_response(response, error_class="service")
    assert "synthetic-secret-sql" not in response.text + caplog.text


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_route_service_unavailable_uses_private_policy(auth_settings, monkeypatch, prefix):
    app = create_app(auth_settings)
    app.dependency_overrides[require_student] = lambda: _student_context()
    app.dependency_overrides[get_db_session] = _fake_database
    app.dependency_overrides[get_clock] = lambda: lambda: NOW

    async def unavailable(*args, **kwargs):
        raise ServiceUnavailable()

    monkeypatch.setattr(availability_service, "get_availability", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        response = await client.get(
            prefix + "/api/v1/availability?purpose_id=00000000-0000-0000-0000-000000000001"
            "&staff_id=00000000-0000-0000-0000-000000000002&date=2026-10-16"
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert_private_response(response, error_class="service")


async def test_availability_returns_public_sorted_utc_slots_without_csrf_or_origin(
    availability_env,
):
    env = availability_env
    freeze_clock(env.app)
    assert (await sign_in(env.client)).status_code == 200
    env.client.cookies.delete("prl_csrf")

    response = await env.client.get(availability_url(env), headers={})

    assert response.status_code == 200
    assert_private_response(response)
    assert response.json() == {
        "date": DAY,
        "timezone": "Asia/Ho_Chi_Minh",
        "slots": [
            {"starts_at": "2026-10-16T01:10:00Z", "ends_at": "2026-10-16T01:40:00Z"},
            {"starts_at": "2026-10-16T01:25:00Z", "ends_at": "2026-10-16T01:55:00Z"},
            {"starts_at": "2026-10-16T01:40:00Z", "ends_at": "2026-10-16T02:10:00Z"},
        ],
    }
    model = AvailabilityResponse.model_validate(response.json())
    instants = [(slot.starts_at.root, slot.ends_at.root) for slot in model.slots]
    assert instants == sorted(set(instants))
    assert all(
        slot.starts_at.root.tzinfo is not None and slot.ends_at.root.tzinfo is not None
        for slot in model.slots
    )
    assert all(
        slot["starts_at"].endswith("Z") and slot["ends_at"].endswith("Z")
        for slot in response.json()["slots"]
    )
    assert set(response.json()) == {"date", "timezone", "slots"}
    assert all(set(slot) == {"starts_at", "ends_at"} for slot in response.json()["slots"])


async def test_availability_identity_comes_from_each_real_session_cookie(availability_env):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    start = datetime(2026, 10, 16, 1, 10, tzinfo=UTC)
    await _add_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.other_staff_id,
        purpose_id=env.other_purpose_id,
        starts_at=start,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://localhost:3000"
    ) as other_client:
        login = await sign_in(other_client, OTHER_STUDENT_CREDENTIALS)
        assert login.status_code == 200
        own = await env.client.get(availability_url(env))
        other = await other_client.get(availability_url(env))

    assert own.status_code == other.status_code == 200
    assert_private_response(own)
    assert_private_response(other)
    assert [slot["starts_at"] for slot in own.json()["slots"]] == ["2026-10-16T01:40:00Z"]
    assert [slot["starts_at"] for slot in other.json()["slots"]] == [
        "2026-10-16T01:10:00Z",
        "2026-10-16T01:25:00Z",
        "2026-10-16T01:40:00Z",
    ]


@pytest.mark.parametrize("role", ["STAFF", "ADMIN"])
async def test_availability_rejects_authenticated_non_students(availability_env, role):
    env = availability_env
    freeze_clock(env.app)
    assert (await sign_in(env.client)).status_code == 200
    async with env.factory.begin() as db:
        user = await db.get(User, env.student_id)
        assert user is not None
        user.role = role

    response = await env.client.get(availability_url(env))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"
    assert_private_response(response, error_class="input")


async def test_availability_requires_valid_active_session(availability_env):
    env = availability_env
    freeze_clock(env.app)
    missing = await env.client.get(availability_url(env))
    unknown = await env.client.get(
        availability_url(env), headers={"Cookie": "prl_session=unknown-token"}
    )
    for response in (missing, unknown):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_SESSION"
        assert_private_response(response, error_class="input")

    assert (await sign_in(env.client)).status_code == 200
    async with env.factory.begin() as db:
        user = await db.get(User, env.student_id)
        assert user is not None
        user.is_active = False
    inactive = await env.client.get(availability_url(env))
    assert inactive.status_code == 401
    assert inactive.json()["error"]["code"] == "INVALID_SESSION"
    assert_private_response(inactive, error_class="input")


@pytest.mark.parametrize("session_state", ["expired", "revoked"])
async def test_availability_rejects_expired_or_revoked_sessions(availability_env, session_state):
    env = availability_env
    freeze_clock(env.app)
    assert (await sign_in(env.client)).status_code == 200
    async with env.factory.begin() as db:
        row = (await db.scalars(select(Session).where(Session.user_id == env.student_id))).one()
        if session_state == "expired":
            row.created_at = datetime.now(UTC) - timedelta(hours=2)
            row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        else:
            row.revoked_at = datetime.now(UTC)

    response = await env.client.get(availability_url(env))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_SESSION"
    assert_private_response(response, error_class="input")


@pytest.mark.parametrize(
    "query",
    [
        "",
        "purpose_id=bad&staff_id=00000000-0000-0000-0000-000000000001&date=2026-10-16",
        "purpose_id=00000000-0000-0000-0000-000000000001&staff_id=00000000-0000-0000-0000-000000000002&date=20261016",
        "purpose_id=00000000-0000-0000-0000-000000000001&staff_id=00000000-0000-0000-0000-000000000002&date=2026-10-16T00:00:00Z",
        "purpose_id=00000000-0000-0000-0000-000000000001&staff_id=00000000-0000-0000-0000-000000000002&date=2026-02-30",
        "purpose_id=00000000-0000-0000-0000-000000000001&staff_id=00000000-0000-0000-0000-000000000002&date=２０２６-１０-１６",
        "purpose_id=00000000-0000-0000-0000-000000000001&staff_id=00000000-0000-0000-0000-000000000002&date=2026-10-16&student_id=attacker",
    ],
)
async def test_availability_malformed_or_extra_query_is_422(availability_env, query):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    response = await env.client.get("/api/v1/availability" + (f"?{query}" if query else ""))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private_response(response, error_class="input")


async def test_availability_duplicate_query_keys_are_422(availability_env):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    query = (
        f"purpose_id={env.purpose_id}&staff_id={env.staff_id}&date={DAY}"
        f"&purpose_id={env.purpose_id}"
    )

    response = await env.client.get("/api/v1/availability?" + query)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private_response(response, error_class="input")


async def test_availability_rejects_dates_outside_booking_window(availability_env):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    for day in ("2026-10-15", "2026-10-23"):
        response = await env.client.get(availability_url(env, day=day))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert_private_response(response, error_class="input")


@pytest.mark.parametrize("missing", ["purpose", "staff"])
async def test_availability_ineligible_entities_are_not_found(availability_env, missing):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    async with env.factory.begin() as db:
        if missing == "purpose":
            purpose = await db.get(Purpose, env.purpose_id)
            assert purpose is not None
            purpose.is_active = False
        else:
            association = await db.get(StaffPurpose, (env.staff_id, env.purpose_id))
            assert association is not None
            association.is_accepting = False

    response = await env.client.get(availability_url(env))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert_private_response(response, error_class="input")


async def test_missing_availability_settings_returns_sanitized_503(availability_env):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    async with env.factory.begin() as db:
        settings = await db.get(ServiceSetting, 1)
        assert settings is not None
        await db.delete(settings)

    response = await env.client.get(availability_url(env))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert_private_response(response, error_class="service")
    assert "timezone" not in response.text


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_availability_service_failure_is_private_and_sanitized(
    availability_env, monkeypatch, caplog, prefix
):
    from app.api.errors import ServiceUnavailable
    from app.services import availability as availability_service

    env = availability_env
    freeze_clock(env.app)
    assert (await sign_in(env.client)).status_code == 200

    async def fail(*args, **kwargs):
        raise ServiceUnavailable()

    monkeypatch.setattr(availability_service, "get_availability", fail)
    token = env.client.cookies["prl_session"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        response = await client.get(
            prefix + availability_url(env), headers={"Cookie": f"prl_session={token}"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert_private_response(response, error_class="service")


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_availability_unexpected_errors_are_sanitized(
    availability_env, monkeypatch, caplog, prefix
):
    import logging

    from app.services import availability as availability_service

    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)

    async def explode(*args, **kwargs):
        raise RuntimeError("synthetic-secret-sql-and-student-data")

    monkeypatch.setattr(availability_service, "get_availability", explode)
    token = env.client.cookies["prl_session"]
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app, root_path=prefix),
            base_url="http://localhost:3000",
        ) as client:
            response = await client.get(
                prefix + availability_url(env), headers={"Cookie": f"prl_session={token}"}
            )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert_private_response(response, error_class="service")
    assert "synthetic-secret-sql-and-student-data" not in response.text + caplog.text


async def test_availability_get_does_not_mutate_application_records(availability_env):
    env = availability_env
    freeze_clock(env.app)
    await sign_in(env.client)
    models = (Appointment, ChangeProposal, AuditEvent, Notification, Session)
    before = [await _rows(env.factory, model) for model in models]

    response = await env.client.get(availability_url(env))

    assert response.status_code == 200
    assert_private_response(response)
    assert [await _rows(env.factory, model) for model in models] == before
