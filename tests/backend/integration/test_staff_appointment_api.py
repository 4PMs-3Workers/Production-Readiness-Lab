from datetime import UTC, datetime, timedelta
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
