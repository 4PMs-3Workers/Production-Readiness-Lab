"""Private caching and whole-request deadlines for catalog and appointment APIs."""

import asyncio

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import AppError, ServiceUnavailable, error_response
from app.core.config import Settings
from app.core.http_body import read_limited_body, replay_body
from app.core.response_policy import requires_request_deadline, uses_private_api_middleware


class PrivateApiHttpMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not uses_private_api_middleware(scope):
            await self.app(scope, receive, send)
            return

        started = False

        async def private_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
            await send(message)

        request = Request(scope)

        async def dispatch() -> None:
            downstream_receive = receive
            if scope["method"] in {"POST", "PUT", "DELETE"} and requires_request_deadline(scope):
                body = await read_limited_body(receive, max_bytes=self.settings.api_max_body_bytes)
                if body is None:
                    return
                downstream_receive = replay_body(body, receive)
            await self.app(scope, downstream_receive, private_send)

        try:
            if requires_request_deadline(scope):
                deadline = asyncio.get_running_loop().time() + self.settings.request_timeout_seconds
                scope.setdefault("state", {})["request_deadline"] = deadline
                async with asyncio.timeout_at(deadline):
                    await dispatch()
            else:
                await dispatch()
        except AppError as exc:
            if started:
                raise
            await error_response(request, exc)(scope, receive, private_send)
        except TimeoutError:
            if started:
                raise
            await error_response(request, ServiceUnavailable())(scope, receive, private_send)
