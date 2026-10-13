"""Request IDs are accepted only from the immediate trusted network peer."""

import re
from ipaddress import IPv4Network, IPv6Network, ip_address
from secrets import token_hex

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_PATTERN = re.compile(rb"[A-Za-z0-9._-]{1,128}\Z")


def get_request_id(request: Request) -> str:
    return request.state.request_id


class RequestIdMiddleware:
    def __init__(
        self, app: ASGIApp, trusted_gateway_cidrs: tuple[IPv4Network | IPv6Network, ...]
    ) -> None:
        self.app = app
        self.trusted_gateway_cidrs = trusted_gateway_cidrs

    def _trusted(self, peer: str | None) -> bool:
        if not peer:
            return False
        try:
            address = ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in self.trusted_gateway_cidrs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        peer = scope.get("client")
        peer_host = peer[0] if peer else None
        incoming = [value for key, value in scope["headers"] if key.lower() == b"x-request-id"]
        request_id = (
            incoming[0].decode("ascii")
            if self._trusted(peer_host)
            and len(incoming) == 1
            and REQUEST_ID_PATTERN.fullmatch(incoming[0])
            else token_hex(16)
        )
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
