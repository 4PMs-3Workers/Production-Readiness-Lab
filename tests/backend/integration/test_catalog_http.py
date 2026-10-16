"""Database-independent HTTP policy checks for catalog routes."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import httpx
import pytest

from app.api import catalog
from app.api.catalog_errors import catalog_error
from app.api.errors import ServiceUnavailable
from app.dependencies.auth import require_auth
from app.dependencies.catalog import require_student
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.factory import create_app
from app.schemas.auth import Role
from app.schemas.catalog import PurposeItem
from app.schemas.common import PageResponse


def student_context(role: Role = "STUDENT"):
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        email="test@example.invalid",
        name="Test User",
        role=role,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def fake_database():
    yield object()


def install_overrides(app, *, role=None):
    app.dependency_overrides[get_db_session] = fake_database
    if role is not None:
        app.dependency_overrides[require_auth] = lambda: student_context(role)


def page():
    return PageResponse[PurposeItem](items=[], page=1, page_size=20, total=0)


@pytest.mark.parametrize("prefix", ["", "/prefix"])
@pytest.mark.parametrize("path", ["/api/v1/purposes", "/api/v1/staff"])
async def test_catalog_success_has_private_cache_and_request_id(
    auth_settings, monkeypatch, prefix, path
):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()
    monkeypatch.setattr(
        catalog.catalog_service, "list_purposes", lambda *args, **kwargs: _resolved(page())
    )
    monkeypatch.setattr(
        catalog.catalog_service, "list_staff", lambda *args, **kwargs: _resolved(page())
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url="http://localhost:3000"
    ) as client:
        response = await client.get(
            prefix + path + ("?purpose_id=" + str(uuid4()) if path.endswith("staff") else "")
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"]


async def _resolved(value):
    return value


@pytest.mark.parametrize("prefix", ["", "/prefix"])
async def test_catalog_auth_role_and_input_errors_are_private(auth_settings, prefix):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_auth] = lambda: student_context("STAFF")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url="http://localhost:3000"
    ) as client:
        unauthorized_app = create_app(auth_settings)
        install_overrides(unauthorized_app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=unauthorized_app, root_path=prefix),
            base_url="http://localhost:3000",
        ) as unauthenticated:
            unauthorized = await unauthenticated.get(prefix + "/api/v1/purposes")
        forbidden = await client.get(prefix + "/api/v1/purposes")
        forbidden_with_extra = await client.get(prefix + "/api/v1/purposes?student_id=attacker")
        method = await client.post(prefix + "/api/v1/purposes")

        valid_app = create_app(auth_settings)
        install_overrides(valid_app)
        valid_app.dependency_overrides[require_student] = lambda: student_context()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=valid_app, root_path=prefix),
            base_url="http://localhost:3000",
        ) as valid_student:
            invalid = await valid_student.get(prefix + "/api/v1/purposes?student_id=attacker")
            duplicate = await valid_student.get(prefix + "/api/v1/purposes?page=1&page=1")

    for response, expected in (
        (unauthorized, 401),
        (forbidden, 403),
        (forbidden_with_extra, 403),
        (invalid, 422),
        (duplicate, 422),
        (method, 405),
    ):
        assert response.status_code == expected
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["X-Request-ID"]
    assert "GET" in method.headers["Allow"]
    assert invalid.json()["error"]["request_id"] == invalid.headers["X-Request-ID"]


@pytest.mark.parametrize("prefix", ["", "/prefix"])
@pytest.mark.parametrize("path", ["/api/v1/purposes", "/api/v1/staff"])
async def test_catalog_service_errors_are_private(auth_settings, monkeypatch, prefix, path):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()

    async def unavailable(*args, **kwargs):
        raise ServiceUnavailable()

    monkeypatch.setattr(catalog.catalog_service, "list_purposes", unavailable)
    monkeypatch.setattr(catalog.catalog_service, "list_staff", unavailable)
    suffix = "?purpose_id=" + str(uuid4()) if path.endswith("staff") else ""

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url="http://localhost:3000"
    ) as client:
        response = await client.get(prefix + path + suffix)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Error-Class"] == "service"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("prefix", ["", "/prefix"])
@pytest.mark.parametrize("path", ["/api/v1/purposes", "/api/v1/staff"])
async def test_catalog_not_found_errors_are_private(auth_settings, monkeypatch, prefix, path):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()

    async def missing(*args, **kwargs):
        raise catalog_error("NOT_FOUND")

    monkeypatch.setattr(catalog.catalog_service, "list_purposes", missing)
    monkeypatch.setattr(catalog.catalog_service, "list_staff", missing)
    suffix = "?purpose_id=" + str(uuid4()) if path.endswith("staff") else ""

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix), base_url="http://localhost:3000"
    ) as client:
        response = await client.get(prefix + path + suffix)

    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Error-Class"] == "input"


@pytest.mark.parametrize("prefix", ["", "/prefix"])
@pytest.mark.parametrize("path", ["/api/v1/purposes", "/api/v1/staff"])
async def test_catalog_timeout_cancels_inflight_service_and_returns_promptly(
    auth_settings, monkeypatch, prefix, path
):
    request_timeout_seconds = 2.0
    app = create_app(
        auth_settings.model_copy(update={"request_timeout_seconds": request_timeout_seconds})
    )
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()
    entered = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    monkeypatch.setattr(catalog.catalog_service, "list_purposes", blocked)
    monkeypatch.setattr(catalog.catalog_service, "list_staff", blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        suffix = "?purpose_id=" + str(uuid4()) if path.endswith("staff") else ""
        started = monotonic()
        response = await client.get(prefix + path + suffix)
    assert entered.is_set()
    assert cleaned_up.is_set()
    assert response.status_code == 503
    assert monotonic() - started < request_timeout_seconds + 0.5
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Error-Class"] == "service"


@pytest.mark.parametrize("prefix", ["", "/prefix"])
async def test_catalog_unexpected_error_is_sanitized_and_outer_fallback_private(
    auth_settings, monkeypatch, caplog, prefix
):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()

    async def explode(*args, **kwargs):
        raise RuntimeError("synthetic-secret-sql")

    monkeypatch.setattr(catalog.catalog_service, "list_purposes", explode)
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path=prefix),
            base_url="http://localhost:3000",
        ) as client:
            response = await client.get(prefix + "/api/v1/purposes")

    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Error-Class"] == "service"
    assert response.headers["X-Request-ID"]
    assert "synthetic-secret-sql" not in response.text + caplog.text


async def test_catalog_slash_redirect_is_private_and_auth_policy_stays_unchanged(auth_settings):
    app = create_app(auth_settings)
    install_overrides(app)
    app.dependency_overrides[require_student] = lambda: student_context()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        redirect = await client.get("/api/v1/purposes/", follow_redirects=False)
        auth_success = await client.get("/api/v1/auth/test-not-found")
        health_method = await client.post("/health/live")

    assert redirect.status_code == 307
    assert redirect.headers["Cache-Control"] == "private, no-store"
    assert redirect.headers["Location"].endswith("/api/v1/purposes")
    assert auth_success.status_code == 404
    assert auth_success.headers["Cache-Control"] == "private, no-store"
    assert health_method.status_code == 405
    assert health_method.headers["Cache-Control"] == "no-store"
