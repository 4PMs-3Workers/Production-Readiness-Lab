"""Database-independent HTTP contracts plus MySQL-gated ownership checks."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_error
from app.api.errors import AppError
from app.core.csrf import issue_csrf_token
from app.dependencies.auth import require_auth
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.factory import create_app
from app.repositories import appointments as appointment_repository
from app.schemas.appointments import AppointmentCreateResponse
from app.schemas.auth import Role
from app.services import booking as booking_service

ORIGIN = "http://localhost:3000"
PURPOSE_ID = UUID("00000000-0000-0000-0000-000000000001")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000002")
APPOINTMENT_ID = UUID("00000000-0000-0000-0000-000000000003")
STARTS_AT = datetime(2026, 10, 16, 1, 10, tzinfo=UTC)
CREATED_AT = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
PAYLOAD = {
    "purpose_id": str(PURPOSE_ID),
    "staff_id": str(STAFF_ID),
    "starts_at": "2026-10-16T01:10:00Z",
    "description": "Discuss course planning",
}


def student_context(role: Role = "STUDENT"):
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="student@example.invalid",
        name="Test Student",
        role=role,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def fake_database():
    yield object()


def install_overrides(app, context=None):
    app.dependency_overrides[get_db_session] = fake_database

    async def authenticate():
        if context is None:
            raise auth_error("INVALID_SESSION")
        return context

    app.dependency_overrides[require_auth] = authenticate


def valid_write_headers(context, *, key="MixedCase-Key:7", origin=ORIGIN):
    token = issue_csrf_token(context.session_id, "test-only-csrf-secret")
    return {
        "Origin": origin,
        "Cookie": f"prl_csrf={token}",
        "X-CSRF-Token": token,
        "Idempotency-Key": key,
    }


def assert_private(response, *, error_class=None):
    assert response.headers["Cache-Control"] == "private, no-store"
    request_id = response.headers["X-Request-ID"]
    assert request_id
    if error_class is not None:
        assert response.headers["X-Error-Class"] == error_class
        assert response.json()["error"]["request_id"] == request_id


@pytest.mark.parametrize("prefix", ["", "/gateway"])
@pytest.mark.parametrize("replayed,expected_status", [(False, 201), (True, 200)])
async def test_create_uses_student_context_exact_key_and_original_receipt(
    auth_settings, monkeypatch, prefix, replayed, expected_status
):
    app = create_app(auth_settings)
    context = student_context()
    install_overrides(app, context)
    receipt = AppointmentCreateResponse(id=APPOINTMENT_ID, status="UPCOMING", created_at=CREATED_AT)
    received = []

    async def create(db, payload, **kwargs):
        received.append((db, payload, kwargs))
        return receipt, replayed

    monkeypatch.setattr(booking_service, "create_appointment", create)
    headers = valid_write_headers(context)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url=ORIGIN
    ) as client:
        response = await client.post(prefix + "/api/v1/appointments", json=PAYLOAD, headers=headers)

    assert response.status_code == expected_status
    assert_private(response)
    assert response.json() == {
        "id": str(APPOINTMENT_ID),
        "status": "UPCOMING",
        "created_at": "2026-10-15T01:00:00Z",
    }
    assert len(received) == 1
    _, payload, kwargs = received[0]
    assert payload.purpose_id == PURPOSE_ID
    assert payload.staff_id == STAFF_ID
    assert payload.description == PAYLOAD["description"]
    assert kwargs["context"] is context
    assert kwargs["idempotency_key"] == "MixedCase-Key:7"
    assert kwargs["request_id"] == response.headers["X-Request-ID"]
    assert kwargs["settings"] is auth_settings
    assert kwargs["deadline"] > asyncio.get_running_loop().time()


async def test_create_rejects_client_owned_and_extra_fields(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    context = student_context()
    install_overrides(app, context)
    called = False

    async def create(*args, **kwargs):
        nonlocal called
        called = True
        return (
            AppointmentCreateResponse(id=APPOINTMENT_ID, status="UPCOMING", created_at=CREATED_AT),
            False,
        )

    monkeypatch.setattr(booking_service, "create_appointment", create)
    payload = PAYLOAD | {"student_id": str(context.user_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/v1/appointments", json=payload, headers=valid_write_headers(context)
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private(response, error_class="input")
    assert not called


async def test_create_requires_idempotency_key(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    context = student_context()
    install_overrides(app, context)
    called = False

    async def create(*args, **kwargs):
        nonlocal called
        called = True
        return (
            AppointmentCreateResponse(id=APPOINTMENT_ID, status="UPCOMING", created_at=CREATED_AT),
            False,
        )

    monkeypatch.setattr(booking_service, "create_appointment", create)
    headers = valid_write_headers(context)
    del headers["Idempotency-Key"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post("/api/v1/appointments", json=PAYLOAD, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private(response, error_class="input")
    assert not called


@pytest.mark.parametrize(
    "context,headers,expected_status,expected_code",
    [
        (None, {"Idempotency-Key": "key-1"}, 401, "INVALID_SESSION"),
        ("STAFF", {"Idempotency-Key": "key-1"}, 403, "FORBIDDEN_ROLE"),
        (
            "STUDENT",
            {"Origin": ORIGIN, "Idempotency-Key": "key-1"},
            403,
            "INVALID_CSRF",
        ),
        (
            "STUDENT",
            {"Origin": "https://attacker.invalid", "Idempotency-Key": "key-1"},
            403,
            "INVALID_ORIGIN",
        ),
    ],
)
async def test_create_requires_student_same_origin_and_csrf(
    auth_settings, context, headers, expected_status, expected_code
):
    app = create_app(auth_settings)
    resolved_context = None if context is None else student_context(context)
    install_overrides(app, resolved_context)
    supplied_headers = dict(headers)
    if context == "STUDENT" and expected_code == "INVALID_ORIGIN":
        supplied_headers = valid_write_headers(
            resolved_context, origin="https://attacker.invalid", key="key-1"
        )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post("/api/v1/appointments", json=PAYLOAD, headers=supplied_headers)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "error",
    [
        AppError(409, "IDEMPOTENCY_CONFLICT", "Key conflict", "business"),
        AppError(409, "BOOKING_CONFLICT", "Slot conflict", "business"),
        AppError(409, "BOOKING_QUOTA_EXCEEDED", "Quota reached", "business"),
        AppError(404, "NOT_FOUND", "Requested booking data was not found", "input"),
        AppError(422, "VALIDATION_ERROR", "Invalid request", "input"),
        AppError(503, "SERVICE_UNAVAILABLE", "Unavailable", "service"),
    ],
)
async def test_create_maps_sanitized_booking_errors_with_private_headers(
    auth_settings, monkeypatch, error
):
    app = create_app(auth_settings)
    context = student_context()
    install_overrides(app, context)

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(booking_service, "create_appointment", fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/v1/appointments", json=PAYLOAD, headers=valid_write_headers(context)
        )

    assert response.status_code == error.status_code
    assert response.json()["error"]["code"] == error.code
    assert_private(response, error_class=error.error_class)


async def test_get_returns_only_current_student_detail_fields(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    context = student_context()
    install_overrides(app, context)
    record = SimpleNamespace(
        id=APPOINTMENT_ID,
        student_id=context.user_id,
        purpose_id=PURPOSE_ID,
        staff_id=STAFF_ID,
        purpose_code="SELECTED",
        purpose_name="Selected purpose",
        staff_name="Selected Staff",
        cancelled_at=None,
        cancel_reason=None,
        duration_minutes=30,
        starts_at=STARTS_AT,
        ends_at=STARTS_AT + timedelta(minutes=30),
        other_reason="Discuss course planning",
        location=None,
        status="UPCOMING",
        created_at=CREATED_AT,
        creation_request_key="secret-key",
        creation_request_hash=b"secret-hash",
    )
    received = []

    async def find(db, *, appointment_id, student_id):
        received.append((db, appointment_id, student_id))
        return record

    monkeypatch.setattr(appointment_repository, "find_student_appointment", find, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/appointments/{APPOINTMENT_ID}")

    assert response.status_code == 200
    assert_private(response)
    assert response.json() == {
        "id": str(APPOINTMENT_ID),
        "purpose_id": str(PURPOSE_ID),
        "staff_id": str(STAFF_ID),
        "purpose_code": "SELECTED",
        "purpose_name": "Selected purpose",
        "staff_name": "Selected Staff",
        "cancelled_at": None,
        "cancel_reason": None,
        "duration_minutes": 30,
        "starts_at": "2026-10-16T01:10:00Z",
        "ends_at": "2026-10-16T01:40:00Z",
        "description": "Discuss course planning",
        "location": None,
        "status": "UPCOMING",
        "created_at": "2026-10-15T01:00:00Z",
    }
    assert {"creation_request_hash", "creation_request_key", "student_id"}.isdisjoint(
        response.json()
    )
    assert len(received) == 1
    assert received[0][1:] == (APPOINTMENT_ID, context.user_id)


async def test_get_missing_or_out_of_scope_appointment_is_private_404(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    install_overrides(app, student_context())

    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(appointment_repository, "find_student_appointment", missing, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/appointments/{APPOINTMENT_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert_private(response, error_class="input")


async def test_get_database_failure_is_a_sanitized_service_error(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    install_overrides(app, student_context())

    async def unavailable(*args, **kwargs):
        raise SQLAlchemyError("synthetic-private-database-detail")

    monkeypatch.setattr(
        appointment_repository, "find_student_appointment", unavailable, raising=False
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/appointments/{APPOINTMENT_ID}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "synthetic-private-database-detail" not in response.text
    assert_private(response, error_class="service")


async def test_appointment_repository_scopes_detail_query_by_id_and_student():
    captured = {}

    class FakeSession:
        async def execute(self, statement):
            captured["statement"] = statement
            return SimpleNamespace(one_or_none=lambda: None)

    result = await appointment_repository.find_student_appointment(
        cast(AsyncSession, FakeSession()), appointment_id=APPOINTMENT_ID, student_id=STAFF_ID
    )
    compiled = captured["statement"].compile(dialect=mysql.dialect())
    sql = str(compiled)

    assert result is None
    assert "appointments.id =" in sql
    assert "appointments.student_id =" in sql
    assert set(compiled.params.values()) == {APPOINTMENT_ID, STAFF_ID}


@pytest.mark.parametrize("method", ["POST", "GET"])
@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_appointment_deadline_covers_post_and_get_work(
    auth_settings, monkeypatch, method, prefix
):
    request_timeout_seconds = 2.0
    app = create_app(
        auth_settings.model_copy(update={"request_timeout_seconds": request_timeout_seconds})
    )
    context = student_context()
    install_overrides(app, context)
    entered = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    if method == "POST":
        monkeypatch.setattr(booking_service, "create_appointment", blocked)
    else:
        monkeypatch.setattr(
            appointment_repository, "find_student_appointment", blocked, raising=False
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url=ORIGIN
    ) as client:
        path = prefix + "/api/v1/appointments"
        started = monotonic()
        if method == "POST":
            response = await client.post(path, json=PAYLOAD, headers=valid_write_headers(context))
        else:
            response = await client.get(path + f"/{APPOINTMENT_ID}")
    assert entered.is_set()
    assert cleaned_up.is_set()
    assert response.status_code == 503
    assert monotonic() - started < request_timeout_seconds + 0.5
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert_private(response, error_class="service")


async def test_appointment_trailing_slash_redirect_is_private(auth_settings):
    app = create_app(auth_settings)
    install_overrides(app, student_context())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(
            f"/api/v1/appointments/{APPOINTMENT_ID}/", follow_redirects=False
        )

    assert response.status_code == 307
    assert response.headers["Location"].endswith(f"/api/v1/appointments/{APPOINTMENT_ID}")
    assert_private(response)


@pytest.mark.parametrize(
    "role,expected_status,expected_code",
    [(None, 401, "INVALID_SESSION"), ("STAFF", 403, "FORBIDDEN_ROLE")],
)
async def test_get_requires_an_authenticated_student(
    auth_settings, role, expected_status, expected_code
):
    app = create_app(auth_settings)
    install_overrides(app, None if role is None else student_context(role))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/appointments/{APPOINTMENT_ID}")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert_private(response, error_class="input")


async def test_get_detail_is_hidden_from_other_students(availability_env):
    """Both the identifier and authenticated owner are part of the real SQL lookup."""
    from app.models import Appointment

    env = availability_env
    own_id, foreign_id = uuid4(), uuid4()
    async with env.factory.begin() as db:
        db.add_all(
            [
                Appointment(
                    id=own_id,
                    student_id=env.student_id,
                    staff_id=env.staff_id,
                    purpose_id=env.purpose_id,
                    duration_minutes=30,
                    starts_at=STARTS_AT,
                    ends_at=STARTS_AT + timedelta(minutes=30),
                    status="UPCOMING",
                    creation_request_key="api-own-" + uuid4().hex,
                    creation_request_hash=sha256(uuid4().bytes).digest(),
                ),
                Appointment(
                    id=foreign_id,
                    student_id=env.other_student_id,
                    staff_id=env.other_staff_id,
                    purpose_id=env.purpose_id,
                    duration_minutes=30,
                    starts_at=STARTS_AT,
                    ends_at=STARTS_AT + timedelta(minutes=30),
                    status="UPCOMING",
                    creation_request_key="api-foreign-" + uuid4().hex,
                    creation_request_hash=sha256(uuid4().bytes).digest(),
                ),
            ]
        )
    primary_login = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": ORIGIN},
    )
    assert primary_login.status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN
    ) as other_client:
        login = await other_client.post(
            "/api/v1/auth/login",
            json={"email": "other-student@example.invalid", "password": "  Good-password!  "},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        own = await env.client.get(f"/api/v1/appointments/{own_id}")
        foreign = await other_client.get(f"/api/v1/appointments/{foreign_id}")
        hidden_from_other = await other_client.get(f"/api/v1/appointments/{own_id}")
        missing = await env.client.get(f"/api/v1/appointments/{uuid4()}")

    assert own.status_code == foreign.status_code == 200
    assert own.json()["id"] == str(own_id)
    assert foreign.json()["id"] == str(foreign_id)
    for response in (own, foreign):
        assert_private(response)
    for response in (hidden_from_other, missing):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert_private(response, error_class="input")
