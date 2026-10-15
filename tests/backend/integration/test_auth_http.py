import httpx
import pytest

from app.factory import create_app


async def test_unexpected_auth_error_is_private_and_sanitized(auth_settings):
    app = create_app(auth_settings)

    @app.get("/api/v1/auth/test-crash")
    async def crash():
        raise RuntimeError("test-sensitive-canary")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        response = await client.get("/api/v1/auth/test-crash")
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "test-sensitive-canary" not in response.text


async def test_chunked_login_limit_counts_bytes_without_content_length(auth_settings):
    app = create_app(auth_settings)

    async def chunks():
        yield b" " * 2048
        yield b" " * 2049

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        response = await client.post(
            "/api/v1/auth/login",
            content=chunks(),
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
        )
    assert response.status_code == 413


@pytest.mark.parametrize("kind,status", [("large", 413), ("type", 415), ("throttle", 429)])
async def test_early_auth_errors_have_shared_headers(auth_settings, kind, status):
    app = create_app(auth_settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
    ) as client:
        headers = {"Origin": "http://localhost:3000", "Content-Type": "application/json"}
        body = b"x" * 4097 if kind == "large" else b"{}"
        if kind == "type":
            headers["Content-Type"] = "text/plain"
        if kind == "throttle":
            for _ in range(30):
                await client.post("/api/v1/auth/login", content=b"{}", headers=headers)
        response = await client.post("/api/v1/auth/login", content=body, headers=headers)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    if kind == "throttle":
        assert int(response.headers["Retry-After"]) >= 1
