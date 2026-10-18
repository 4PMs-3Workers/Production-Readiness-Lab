"""Tests for the private API's whole-request deadline wiring."""

import asyncio

from starlette.types import Message, Receive, Scope, Send

from app.core.catalog_http import PrivateApiHttpMiddleware
from app.core.response_policy import (
    requires_private_response,
    requires_request_deadline,
    uses_private_api_middleware,
)


def test_staff_appointment_routes_use_private_headers_and_request_deadlines():
    for method, path in (
        ("GET", "/api/v1/staff/me/appointments"),
        ("GET", "/api/v1/staff/me/appointments/00000000-0000-0000-0000-000000000001"),
        ("POST", "/api/v1/staff/me/appointments/00000000-0000-0000-0000-000000000001/cancel"),
        ("POST", "/api/v1/staff/me/appointments/00000000-0000-0000-0000-000000000001/outcome"),
    ):
        scope: Scope = {"type": "http", "method": method, "path": path, "root_path": ""}
        assert uses_private_api_middleware(scope)
        assert requires_private_response(scope)
        assert requires_request_deadline(scope)


async def test_appointment_middleware_uses_the_configured_stress_deadline(auth_settings):
    settings = auth_settings.model_copy(update={"request_timeout_seconds": 20.0})
    captured = {}

    async def app(scope: Scope, _receive: Receive, _send: Send) -> None:
        captured["deadline"] = scope["state"]["request_deadline"]
        captured["observed_at"] = asyncio.get_running_loop().time()

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: Message) -> None:
        return None

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/appointments",
        "raw_path": b"/api/v1/appointments",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 3000),
        "state": {},
    }
    started_at = asyncio.get_running_loop().time()
    middleware = PrivateApiHttpMiddleware(app, settings=settings)

    await middleware(scope, receive, send)

    assert captured["deadline"] > captured["observed_at"]
    # Deadline creation falls between the pre-call and in-app clock samples.
    assert started_at + 20.0 <= captured["deadline"] <= captured["observed_at"] + 20.0
