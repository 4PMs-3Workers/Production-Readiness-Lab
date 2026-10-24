"""Body limits precede JSON parsing and authentication, regardless of framing."""

import httpx
import pytest
from starlette.requests import Request

from app.dependencies.database import get_db_session
from app.factory import create_app

APPOINTMENT = "00000000-0000-0000-0000-000000000001"
WRITE_PATHS = [
    "/api/v1/appointments",
    f"/api/v1/appointments/{APPOINTMENT}/cancel",
    f"/api/v1/staff/me/appointments/{APPOINTMENT}/cancel",
    f"/api/v1/staff/me/appointments/{APPOINTMENT}/outcome",
    f"/api/v1/notifications/{APPOINTMENT}/read",
    "/api/v1/auth/logout",
]


async def no_database():
    yield object()


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("prefix", ["", "/prefix"])
@pytest.mark.parametrize("length", [None, "1", "999999"])
async def test_oversized_unauthenticated_posts_never_reach_json_parser(
    auth_settings, monkeypatch, path, prefix, length
):
    app = create_app(auth_settings)
    app.dependency_overrides[get_db_session] = no_database
    parsed = []
    original_json = Request.json

    async def track_json(request):
        parsed.append(len(await request.body()))
        return await original_json(request)

    monkeypatch.setattr(Request, "json", track_json)

    async def chunks():
        yield b'{"description":"'
        yield b"x" * 32768
        yield b'"}'

    headers = {"Content-Type": "application/json", "Origin": "http://localhost:3000"}
    if length is not None:
        headers["Content-Length"] = length
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        response = await client.post(prefix + path, content=chunks(), headers=headers)

    assert parsed == []
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Error-Class"] == "input"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "path,size,status",
    [
        ("/api/v1/appointments", 32767, 401),
        ("/api/v1/appointments", 32768, 401),
        (f"/api/v1/notifications/{APPOINTMENT}/read", 32768, 401),
        ("/api/v1/auth/logout", 32767, 204),
        ("/api/v1/auth/logout", 32768, 204),
        ("/api/v1/auth/login", 4095, 422),
        ("/api/v1/auth/login", 4096, 422),
    ],
)
async def test_chunked_bodies_at_or_below_limit_preserve_route_behavior(
    auth_settings, path, size, status
):
    app = create_app(auth_settings)
    app.dependency_overrides[get_db_session] = no_database

    async def chunks():
        yield b"{"
        yield b" " * (size - 2)
        yield b"}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        response = await client.post(
            path,
            content=chunks(),
            headers={"Content-Type": "application/json", "Origin": "http://localhost:3000"},
        )
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/auth/login/"])
@pytest.mark.parametrize("prefix", ["", "/prefix"])
async def test_login_retains_tighter_limit_even_before_slash_redirect(auth_settings, path, prefix):
    app = create_app(auth_settings)

    async def chunks():
        yield b" " * 4096
        yield b" "

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path=prefix),
        base_url="http://localhost:3000",
    ) as client:
        response = await client.post(
            prefix + path,
            content=chunks(),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost:3000",
                "Content-Length": "1",
            },
        )
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "private, no-store"


async def test_configured_api_limit_is_used_for_business_posts(auth_settings):
    app = create_app(auth_settings.model_copy(update={"api_max_body_bytes": 8}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        response = await client.post("/api/v1/appointments", content=b"123456789")
    assert response.status_code == 413
