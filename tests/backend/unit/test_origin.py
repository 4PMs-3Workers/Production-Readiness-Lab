import pytest
from starlette.requests import Request

from app.api.errors import AppError


def request(headers):
    return Request({"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers]})


def test_origin_default_ports():
    from app.core.origin import parse_origin

    assert parse_origin("https://EXAMPLE.invalid:443") == ("https", "example.invalid", 443)
    assert parse_origin("https://example.invalid") == ("https", "example.invalid", 443)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("origin", "null")],
        [("origin", "http://localhost:3000.evil.invalid")],
        [("origin", "http://localhost:3001")],
        [("origin", "http://localhost:3000/x")],
        [("origin", "http://a@localhost:3000")],
        [("origin", "http://localhost:3000\\evil")],
        [("origin", "http://evil.invalid"), ("referer", "http://localhost:3000/x")],
        [("origin", "http://localhost:3000"), ("origin", "http://localhost:3000")],
    ],
)
def test_invalid_origin_is_rejected(headers, auth_settings):
    from app.core.origin import require_same_origin

    with pytest.raises(AppError) as caught:
        require_same_origin(request(headers), auth_settings)
    assert caught.value.code == "INVALID_ORIGIN"


def test_referer_is_only_fallback(auth_settings):
    from app.core.origin import require_same_origin

    require_same_origin(request([("referer", "http://localhost:3000/path?q=1")]), auth_settings)
    require_same_origin(request([("origin", "http://LOCALHOST:3000")]), auth_settings)
