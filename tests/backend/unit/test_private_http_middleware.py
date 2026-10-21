"""Tests for the private API's whole-request deadline wiring."""

import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from app.core.auth_http import AuthHttpMiddleware
from app.core.catalog_http import PrivateApiHttpMiddleware
from app.core.login_throttle import LoginThrottle
from app.core.response_policy import (
    requires_private_response,
    requires_request_deadline,
    uses_private_api_middleware,
)


def test_staff_appointment_routes_use_private_headers_and_request_deadlines():
    for method, path in (
        ("GET", "/api/v1/staff/me/appointments"),
        ("GET", "/api/v1/staff/me/context"),
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


def body_scope(path):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "root_path": "",
        "headers": [
            (b"origin", b"http://localhost:3000"),
            (b"content-type", b"application/json"),
        ],
        "state": {"request_id": "test-body-limit"},
    }


def body_middleware(app, path, settings):
    if "/auth/" in path:
        return AuthHttpMiddleware(app, settings=settings, throttle=LoginThrottle(settings))
    return PrivateApiHttpMiddleware(app, settings=settings)


@pytest.mark.parametrize("path", ["/api/v1/purposes", "/api/v1/students/me"])
async def test_unsupported_posts_do_not_wait_for_request_body(auth_settings, path):
    sent = []

    async def receive():
        raise AssertionError("Unsupported POST must reach method rejection without reading a body")

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await Response(status_code=405)(scope, receive, send)

    await PrivateApiHttpMiddleware(app, settings=auth_settings)(body_scope(path), receive, send)

    assert sent[0]["status"] == 405


@pytest.mark.parametrize(
    "path", ["/api/v1/appointments", "/api/v1/auth/logout", "/api/v1/auth/login"]
)
async def test_body_disconnect_stops_before_routing(auth_settings, path):
    messages = iter(
        [
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    routed = False
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        nonlocal routed
        routed = True
        await Response(status_code=204)(scope, receive, send)

    await body_middleware(app, path, auth_settings)(body_scope(path), receive, send)

    assert not routed
    assert sent == []


@pytest.mark.parametrize(
    "path", ["/api/v1/appointments", "/api/v1/auth/logout", "/api/v1/auth/login"]
)
async def test_buffered_body_replays_once_then_preserves_disconnect(auth_settings, path):
    messages = iter(
        [
            {"type": "http.request", "body": b'{"value":', "more_body": True},
            {"type": "http.request", "body": b"123}", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    observed = {}

    async def receive():
        return next(messages)

    async def send(message):
        pass

    async def app(scope, receive, send):
        observed["json"] = await Request(scope, receive).json()
        observed["next"] = await receive()

    await body_middleware(app, path, auth_settings)(body_scope(path), receive, send)

    assert observed == {"json": {"value": 123}, "next": {"type": "http.disconnect"}}


@pytest.mark.parametrize(
    "path", ["/api/v1/appointments", "/api/v1/auth/logout", "/api/v1/auth/login"]
)
async def test_body_read_is_inside_existing_request_deadline(auth_settings, path):
    settings = auth_settings.model_copy(update={"request_timeout_seconds": 0.01})
    sent = []
    routed = False
    cancelled = False

    async def receive():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        nonlocal routed
        routed = True
        await Response(status_code=204)(scope, receive, send)

    await body_middleware(app, path, settings)(body_scope(path), receive, send)

    assert not routed
    assert cancelled
    assert sent[0]["status"] == 503
    assert dict(sent[0]["headers"])[b"cache-control"] == b"private, no-store"
