"""HTTP security and failure contracts without a MySQL dependency."""

import asyncio
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

from app.core.errors import ServiceUnavailable
from app.domain.auth import AuthContext
from app.factory import create_app
from app.schemas.auth import Role
from app.services import staff_availability as service

BASE = "/api/v1/staff/me"
ID = uuid4()
ROUTES = [
    ("GET", "/purposes", None),
    ("PUT", f"/purposes/{ID}", {"is_accepting": True}),
    ("GET", "/unavailability", None),
    (
        "POST",
        "/unavailability",
        {"starts_at": "2026-01-01T00:00:00Z", "ends_at": "2026-01-02T00:00:00Z"},
    ),
    ("DELETE", f"/unavailability/{ID}", None),
]


def context(role: Role = "STAFF") -> AuthContext:
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="staff@example.invalid",
        name="Staff",
        role=role,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.parametrize("role,status", [(None, 401), ("STUDENT", 403), ("ADMIN", 403)])
async def test_every_configuration_route_requires_staff(auth_settings, role, status):
    app = create_app(auth_settings)
    install_overrides(app, context(role) if role else None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for method, path, body in ROUTES:
            response = await client.request(method, BASE + path, json=body)
            assert response.status_code == status
            assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "mode,code",
    [("missing", "INVALID_CSRF"), ("foreign", "INVALID_ORIGIN"), ("wrong-session", "INVALID_CSRF")],
)
async def test_all_writes_require_origin_and_session_signed_csrf(auth_settings, mode, code):
    app = create_app(auth_settings)
    actor = context()
    install_overrides(app, actor)
    headers = valid_write_headers(context() if mode == "wrong-session" else actor)
    if mode == "missing":
        headers = {"Origin": ORIGIN}
    elif mode == "foreign":
        headers["Origin"] = "https://attacker.invalid"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for method, path, body in ROUTES:
            if method == "GET":
                continue
            response = await client.request(method, BASE + path, json=body, headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == code
            assert_private(response, error_class="input")


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_lists_apply_private_deadlines_and_sanitized_failures(
    auth_settings, monkeypatch, prefix
):
    settings = auth_settings.model_copy(update={"request_timeout_seconds": 0.03})
    app = create_app(settings)
    install_overrides(app, context())

    async def unavailable(*args, **kwargs):
        raise ServiceUnavailable()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url=ORIGIN
    ) as client:
        for operation in (unavailable, slow):
            for name, path in [("list_purposes", "/purposes"), ("list_periods", "/unavailability")]:
                monkeypatch.setattr(service, name, operation)
                response = await client.get(prefix + BASE + path)
                assert response.status_code == 503
                assert_private(response, error_class="service")


async def test_strict_query_body_and_delete_input_contracts(auth_settings):
    app = create_app(auth_settings)
    actor = context()
    install_overrides(app, actor)
    headers = valid_write_headers(actor)
    cases = [
        ("GET", "/purposes?page=0", None),
        ("GET", "/purposes?page_size=101", None),
        ("GET", "/purposes?page=1&page=2", None),
        ("GET", "/purposes?staff_id=forged", None),
        ("GET", "/unavailability?from=2026-01-01", None),
        ("GET", "/unavailability?to=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z", None),
        ("GET", "/unavailability?from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z", None),
        ("PUT", f"/purposes/{ID}", {"is_accepting": "true"}),
        ("PUT", f"/purposes/{ID}", {"is_accepting": True, "actor_id": str(ID)}),
        (
            "POST",
            "/unavailability",
            {"starts_at": "2026-01-01T00:00:00", "ends_at": "2026-01-02T00:00:00Z"},
        ),
        ("POST", "/unavailability?staff_id=forged", ROUTES[3][2]),
        ("DELETE", f"/unavailability/{ID}", {"actor_id": str(ID)}),
        ("DELETE", f"/unavailability/{ID}?staff_id=forged", None),
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        for method, path, body in cases:
            response = await client.request(method, BASE + path, json=body, headers=headers)
            assert response.status_code == 422, (method, path)
            assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "method,path",
    [("PUT", f"/purposes/{ID}"), ("POST", "/unavailability"), ("DELETE", f"/unavailability/{ID}")],
)
async def test_configuration_payloads_are_bounded_before_parsing(auth_settings, method, path):
    app = create_app(auth_settings.model_copy(update={"api_max_body_bytes": 32}))
    actor = context()
    install_overrides(app, actor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.request(
            method, BASE + path, content=b"x" * 33, headers=valid_write_headers(actor)
        )
    assert response.status_code == 413
    assert_private(response, error_class="input")


@pytest.mark.parametrize("prefix", ["", "/gateway"])
async def test_each_write_has_a_whole_request_deadline(auth_settings, monkeypatch, prefix):
    app = create_app(auth_settings.model_copy(update={"request_timeout_seconds": 0.03}))
    current = context()
    install_overrides(app, current)

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    for name in ("set_purpose", "create_period", "delete_period"):
        monkeypatch.setattr(service, name, slow)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url=ORIGIN
    ) as client:
        for method, path, body in ROUTES:
            if method == "GET":
                continue
            response = await client.request(
                method, prefix + BASE + path, json=body, headers=valid_write_headers(current)
            )
            assert response.status_code == 503
            assert_private(response, error_class="service")


async def test_unexpected_errors_keep_private_policy_and_do_not_echo_sensitive_values(
    auth_settings, monkeypatch, caplog
):
    app = create_app(auth_settings)
    install_overrides(app, context())
    marker = "private-token-and-consultation-content"

    async def failed(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(service, "list_periods", failed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=ORIGIN
    ) as client:
        response = await client.get(BASE + "/unavailability")
    assert response.status_code == 500
    assert_private(response, error_class="service")
    assert marker not in response.text and marker not in caplog.text
