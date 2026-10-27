"""Request identity is accepted only from the immediate trusted network peer."""

import re
from ipaddress import IPv4Network, IPv6Network, ip_address
from secrets import token_hex

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_PATTERN = re.compile(rb"[A-Za-z0-9._-]{1,128}\Z")
GatewayNetworks = tuple[IPv4Network | IPv6Network, ...]
UNTRUSTED_TRACE_HEADERS = frozenset({b"traceparent", b"tracestate", b"baggage"})


def get_request_id(request: Request) -> str:
    return request.state.request_id


def get_client_ip(request: Request) -> str:
    """Return the validated client IP, falling back to the immediate socket peer."""
    forwarded = request.scope.get("state", {}).get("trusted_client_ip")
    if isinstance(forwarded, str):
        return forwarded
    peer = request.client
    return peer.host if peer is not None else "unknown"


def _is_trusted(peer: str | None, networks: GatewayNetworks) -> bool:
    if not peer:
        return False
    try:
        address = ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _single_header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    values = [value for key, value in headers if key.lower() == name]
    return values[0] if len(values) == 1 else None


def _canonical_ip(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        text = value.decode("ascii")
        address = ip_address(text)
    except (UnicodeDecodeError, ValueError):
        return None
    canonical = str(address)
    return canonical if text == canonical else None


class TrustedGatewayMiddleware:
    """Use forwarding identity only when the immediate socket peer is trusted."""

    def __init__(self, app: ASGIApp, trusted_gateway_cidrs: GatewayNetworks) -> None:
        self.app = app
        self.trusted_gateway_cidrs = trusted_gateway_cidrs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        peer = scope.get("client")
        peer_host = peer[0] if peer else None
        client_ip = peer_host or "unknown"
        if _is_trusted(peer_host, self.trusted_gateway_cidrs):
            headers = scope.get("headers", [])
            client_ip = _canonical_ip(_single_header(headers, b"x-forwarded-for")) or client_ip
            forwarded_proto = _single_header(headers, b"x-forwarded-proto")
            if forwarded_proto in {b"http", b"https"}:
                scope["scheme"] = forwarded_proto.decode("ascii")

        scope.setdefault("state", {})["trusted_client_ip"] = client_ip
        await self.app(scope, receive, send)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp, trusted_gateway_cidrs: GatewayNetworks) -> None:
        self.app = app
        self.trusted_gateway_cidrs = trusted_gateway_cidrs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        peer = scope.get("client")
        peer_host = peer[0] if peer else None
        # The API always creates a new trace root; remote context and baggage are ignored.
        scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in UNTRUSTED_TRACE_HEADERS
        ]
        incoming = [value for key, value in scope["headers"] if key.lower() == b"x-request-id"]
        request_id = (
            incoming[0].decode("ascii")
            if _is_trusted(peer_host, self.trusted_gateway_cidrs)
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
