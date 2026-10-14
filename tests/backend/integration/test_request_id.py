import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.factory import create_app


def make_app(trusted: str = "127.0.0.0/8"):
    return create_app(
        Settings.model_validate(
            {
                "DATABASE_URL": "mysql+asyncmy://user:password@127.0.0.1:1/prl",
                "APPLICATION_ORIGIN": "http://localhost:3000",
                "COOKIE_SECRET": "cookie-secret",
                "CSRF_SECRET": "csrf-secret",
                "ACCEPTED_SCHEMA_REVISIONS": "a1b2c3d4e5f6",
                "TRUSTED_GATEWAY_CIDRS": trusted,
            }
        )
    )


def test_trusted_peer_keeps_single_valid_request_id():
    with TestClient(make_app(), client=("127.0.0.1", 12345)) as client:
        result = client.get("/missing", headers={"X-Request-ID": "abc_12-Z"})
    assert result.headers["X-Request-ID"] == "abc_12-Z"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("X-Request-ID", "")],
        [("X-Request-ID", "has space")],
        [("X-Request-ID", b"\xe9")],
        [("X-Request-ID", "x" * 129)],
        [("X-Request-ID", "one"), ("X-Request-ID", "two")],
    ],
)
def test_invalid_or_duplicate_header_is_replaced(headers):
    with TestClient(make_app(), client=("127.0.0.1", 12345)) as client:
        result = client.get("/missing", headers=headers)
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", result.headers["X-Request-ID"])
    assert result.headers["X-Request-ID"] not in {"one", "two", "x" * 129}


@pytest.mark.parametrize("trusted", ["", "10.0.0.0/8"])
def test_untrusted_peer_cannot_supply_id_even_with_forwarded_for(trusted):
    with TestClient(make_app(trusted), client=("127.0.0.1", 12345)) as client:
        result = client.get(
            "/missing",
            headers={"X-Request-ID": "client-chosen", "X-Forwarded-For": "10.1.1.1"},
        )
    assert result.headers["X-Request-ID"] != "client-chosen"
