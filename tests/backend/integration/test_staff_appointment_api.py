import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module, util
from uuid import uuid4

import httpx
import pytest
from tests.backend.integration.test_appointments_api import (
    ORIGIN,
    assert_private,
    install_overrides,
    valid_write_headers,
)

from app.domain.auth import AuthContext
from app.factory import create_app
from app.schemas.auth import Role
from app.schemas.common import PageResponse, UtcTimestamp
from app.schemas.staff_appointments import (
    StaffAppointmentDetailResponse,
    StaffAppointmentListItem,
    StaffAppointmentOutcomeResponse,
)


def staff_context(role: Role = "STAFF"):
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="staff@example.invalid",
        name="Test Staff",
        role=role,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.parametrize("role,status", [(None, 401), ("STUDENT", 403), ("ADMIN", 403)])
async def test_staff_list_requires_staff_role(auth_settings, role, status):
    app = create_app(auth_settings)
    install_overrides(app, staff_context(role) if role else None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get("/api/v1/staff/me/appointments")
    assert response.status_code == status
    assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "query",
    ["page=1&page=2", "page_size=1&page_size=2", "status=UPCOMING&status=CANCELLED", "actor=x"],
)
async def test_staff_list_rejects_duplicate_and_extra_query_parameters(auth_settings, query):
    app = create_app(auth_settings)
    install_overrides(app, staff_context())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get("/api/v1/staff/me/appointments?" + query)
    assert response.status_code == 422
    assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "origin,csrf,expected",
    [(ORIGIN, False, "INVALID_CSRF"), ("https://attacker.invalid", True, "INVALID_ORIGIN")],
)
async def test_staff_post_requires_origin_and_signed_csrf(auth_settings, origin, csrf, expected):
    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    headers = {"Origin": origin}
    if csrf:
        headers.update(valid_write_headers(context, origin=origin))
    appointment_id = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/api/v1/staff/me/appointments/{appointment_id}/outcome",
            json={"status": "COMPLETED"},
            headers=headers,
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == expected
    assert_private(response, error_class="input")


async def test_staff_outcome_rejects_client_actor_and_unknown_status(auth_settings):
    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    headers = valid_write_headers(context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/api/v1/staff/me/appointments/{uuid4()}/outcome",
            json={"status": "NO_SHOW", "actor_id": str(context.user_id)},
            headers=headers,
        )
    assert response.status_code == 422
    assert_private(response, error_class="input")


def _staff_service():
    assert (
        util.find_spec("app.services.staff_appointments") is not None
    ), "staff read service is missing"
    return import_module("app.services.staff_appointments")


async def test_staff_list_routes_session_actor_and_filters(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    received = []
    page = PageResponse[StaffAppointmentListItem](items=[], total=0, page=2, page_size=5)

    async def list_appointments(db, query, *, staff_id, settings):
        received.append((db, query, staff_id, settings))
        return page

    monkeypatch.setattr(_staff_service(), "list_staff_appointments", list_appointments)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(
            "/api/v1/staff/me/appointments?page=2&page_size=5&status=COMPLETED&date=2026-10-20"
        )
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 2, "page_size": 5}
    assert received[0][1].status == "COMPLETED"
    assert received[0][1].date.isoformat() == "2026-10-20"
    assert received[0][2] == context.user_id
    assert_private(response)


async def test_staff_detail_routes_only_the_authenticated_assignment(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    appointment_id = uuid4()
    result = StaffAppointmentDetailResponse(
        id=appointment_id,
        purpose_id=uuid4(),
        purpose_code="TEST",
        purpose_name="Test purpose",
        student_id=uuid4(),
        student_name="Test student",
        duration_minutes=30,
        starts_at=UtcTimestamp(datetime(2026, 10, 20, 7, tzinfo=UTC)),
        ends_at=UtcTimestamp(datetime(2026, 10, 20, 7, 30, tzinfo=UTC)),
        location="Room 2",
        status="UPCOMING",
        created_at=UtcTimestamp(datetime(2026, 10, 15, tzinfo=UTC)),
        cancelled_at=None,
        outcome_at=None,
        description="private detail",
        cancelled_by=None,
        cancel_reason=None,
        outcome_by=None,
    )
    received = []

    async def get_appointment(db, *, appointment_id, staff_id, settings):
        received.append((appointment_id, staff_id))
        return result

    monkeypatch.setattr(_staff_service(), "get_staff_appointment", get_appointment)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/staff/me/appointments/{appointment_id}")
    assert response.status_code == 200
    assert response.json()["description"] == "private detail"
    assert received == [(appointment_id, context.user_id)]
    assert_private(response)


@pytest.mark.parametrize("query", ["actor=unexpected", "page=1&page=1"])
async def test_staff_detail_rejects_unrecognized_or_repeated_query(auth_settings, query):
    app = create_app(auth_settings)
    install_overrides(app, staff_context())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(f"/api/v1/staff/me/appointments/{uuid4()}?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_private(response, error_class="input")


async def test_staff_cancel_routes_session_actor_and_private_reason(auth_settings, monkeypatch):
    from app.schemas.appointments import AppointmentCancelResponse
    from app.services import cancellation

    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    appointment_id = uuid4()
    received = []

    async def cancel(db, **kwargs):
        received.append(kwargs)
        return AppointmentCancelResponse(
            id=appointment_id,
            status="CANCELLED",
            cancelled_at=datetime(2026, 10, 15, 1, tzinfo=UTC),
        )

    monkeypatch.setattr(cancellation, "cancel_staff_appointment", cancel)
    headers = valid_write_headers(context)
    del headers["Idempotency-Key"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/api/v1/staff/me/appointments/{appointment_id}/cancel",
            json={"reason": "private reason"},
            headers=headers,
        )
    assert response.status_code == 200
    assert received[0]["context"] is context
    assert received[0]["payload"].reason == "private reason"
    assert received[0]["request_id"] == response.headers["X-Request-ID"]
    assert received[0]["deadline"] > asyncio.get_running_loop().time()
    assert_private(response)


async def test_staff_outcome_routes_session_actor_and_payload(auth_settings, monkeypatch):
    from app.services import staff_appointments

    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    appointment_id = uuid4()
    received = []

    async def record(db, *, appointment_id, outcome, context, request_id, settings, deadline):
        received.append((appointment_id, outcome.status, context, request_id, deadline))
        return StaffAppointmentOutcomeResponse(
            id=appointment_id,
            status=outcome.status,
            outcome_at=UtcTimestamp(datetime(2026, 10, 20, 8, tzinfo=UTC)),
        )

    monkeypatch.setattr(staff_appointments, "record_staff_appointment_outcome", record)
    headers = valid_write_headers(context)
    del headers["Idempotency-Key"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/api/v1/staff/me/appointments/{appointment_id}/outcome",
            json={"status": "NO_SHOW"},
            headers=headers,
        )
    assert response.status_code == 200
    assert received[0][:3] == (appointment_id, "NO_SHOW", context)
    assert received[0][3] == response.headers["X-Request-ID"]
    assert received[0][4] > asyncio.get_running_loop().time()
    assert response.json()["status"] == "NO_SHOW"
    assert_private(response)
