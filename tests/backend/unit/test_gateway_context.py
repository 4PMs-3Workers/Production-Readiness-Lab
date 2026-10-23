"""Trusted gateway forwarding is parsed without changing the immediate ASGI peer."""

from ipaddress import ip_network

import pytest
from starlette.requests import Request

from app.core.request_context import TrustedGatewayMiddleware, get_client_ip

TRUSTED = (ip_network("172.30.34.0/29"),)


def make_scope(peer="172.30.34.2", headers=(), scheme="http"):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (
                name.lower().encode() if isinstance(name, str) else name.lower(),
                value.encode() if isinstance(value, str) else value,
            )
            for name, value in headers
        ],
        "client": (peer, 12345),
        "server": ("api", 8000),
        "state": {},
    }


async def observe(scope):
    result = {}

    async def app(current, _receive, _send):
        request = Request(current)
        result.update(
            client_ip=get_client_ip(request),
            scheme=current["scheme"],
            client=current["client"],
        )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    await TrustedGatewayMiddleware(app, TRUSTED)(scope, receive, send)
    return result


@pytest.mark.parametrize("forwarded_ip", ["198.51.100.7", "2001:db8::1"])
async def test_trusted_gateway_accepts_one_canonical_forwarded_ip(forwarded_ip):
    scope = make_scope(headers=[("X-Forwarded-For", forwarded_ip)])

    observed = await observe(scope)

    assert observed["client_ip"] == forwarded_ip
    assert observed["client"] == ("172.30.34.2", 12345)


@pytest.mark.parametrize(
    "values",
    [
        [],
        [b"198.51.100.7, 198.51.100.8"],
        [b"198.51.100.7", b"198.51.100.8"],
        [b" 198.51.100.7"],
        [b"2001:0db8::1"],
        [b"198.051.100.7"],
    ],
)
async def test_malformed_or_duplicate_forwarded_ip_falls_back_to_gateway_peer(values):
    scope = make_scope(headers=[(b"x-forwarded-for", value) for value in values])

    observed = await observe(scope)

    assert observed["client_ip"] == "172.30.34.2"


async def test_untrusted_peer_cannot_supply_client_ip_or_scheme():
    scope = make_scope(
        peer="10.0.0.5",
        headers=[("X-Forwarded-For", "198.51.100.7"), ("X-Forwarded-Proto", "https")],
    )

    observed = await observe(scope)

    assert observed["client_ip"] == "10.0.0.5"
    assert observed["scheme"] == "http"


@pytest.mark.parametrize(
    "proto_values,original_scheme",
    [([], "http"), ([b"fake"], "https"), ([b"http", b"https"], "http"), ([b"HTTPS"], "http")],
)
async def test_missing_malformed_or_duplicate_proto_falls_back_independently(
    proto_values, original_scheme
):
    headers = [(b"x-forwarded-for", b"203.0.113.9")]
    headers.extend((b"x-forwarded-proto", value) for value in proto_values)
    scope = make_scope(headers=headers, scheme=original_scheme)

    observed = await observe(scope)

    assert observed["client_ip"] == "203.0.113.9"
    assert observed["scheme"] == original_scheme


@pytest.mark.parametrize("proto", ["http", "https"])
async def test_trusted_gateway_accepts_exactly_one_http_scheme(proto):
    scope = make_scope(headers=[("X-Forwarded-Proto", proto)])

    observed = await observe(scope)

    assert observed["scheme"] == proto
    assert observed["client"] == ("172.30.34.2", 12345)


async def test_valid_proto_is_used_when_forwarded_ip_is_invalid():
    scope = make_scope(headers=[("X-Forwarded-For", "not-an-ip"), ("X-Forwarded-Proto", "https")])

    observed = await observe(scope)

    assert observed["client_ip"] == "172.30.34.2"
    assert observed["scheme"] == "https"
