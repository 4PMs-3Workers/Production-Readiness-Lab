"""Auth HTTP limits run before JSON parsing and preserve the shared error contract."""

import asyncio

from starlette._utils import get_route_path
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.auth_errors import auth_error
from app.api.errors import AppError, ServiceUnavailable, error_response
from app.core.config import Settings
from app.core.http_body import read_limited_body, replay_body
from app.core.login_throttle import LoginThrottle
from app.core.origin import require_same_origin


class AuthHttpMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings, throttle: LoginThrottle) -> None:
        self.app, self.settings, self.throttle = app, settings, throttle

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        route_path = get_route_path(scope)
        if not route_path.startswith("/api/v1/auth/"):
            await self.app(scope, receive, send)
            return
        started = False

        async def auth_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
            await send(message)

        request = Request(scope)
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                path = route_path.rstrip("/")
                downstream_receive = receive
                if path == "/api/v1/auth/login" and scope["method"] == "POST":
                    self.throttle.check_ip(request.client.host if request.client else "unknown")
                    require_same_origin(request, self.settings)
                    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
                    if media != "application/json":
                        raise auth_error("UNSUPPORTED_MEDIA_TYPE")
                    body = await read_limited_body(
                        receive, max_bytes=self.settings.login_max_body_bytes
                    )
                    if body is None:
                        return
                    downstream_receive = replay_body(body, receive)
                elif path == "/api/v1/auth/logout" and scope["method"] == "POST":
                    body = await read_limited_body(
                        receive, max_bytes=self.settings.api_max_body_bytes
                    )
                    if body is None:
                        return
                    downstream_receive = replay_body(body, receive)
                await self.app(scope, downstream_receive, auth_send)
        except AppError as exc:
            if started:
                raise
            await error_response(request, exc)(scope, receive, auth_send)
        except TimeoutError:
            if started:
                raise
            await error_response(request, ServiceUnavailable())(scope, receive, auth_send)
