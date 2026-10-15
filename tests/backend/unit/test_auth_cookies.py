from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest
from fastapi import Response


@pytest.mark.parametrize("secure", [False, True])
def test_cookie_issue_and_delete_scope(auth_settings, secure):
    from app.core.auth_cookies import clear_auth_cookies, set_auth_cookies

    now = datetime(2026, 10, 15, tzinfo=UTC)
    auth_settings = auth_settings.model_copy(update={"cookie_secure": secure})
    response = Response()
    set_auth_cookies(
        response,
        session_token="session",
        csrf_token="csrf",
        expires_at=now + timedelta(seconds=100),
        now=now,
        settings=auth_settings,
    )
    cookies = SimpleCookie()
    for value in response.headers.getlist("set-cookie"):
        cookies.load(value)
    assert cookies["prl_session"]["httponly"]
    assert not cookies["prl_csrf"]["httponly"]
    for value in cookies.values():
        assert value["path"] == "/"
        assert value["domain"] == ""
        assert value["samesite"] == "lax"
        assert value["max-age"] == "100"
        assert bool(value["secure"]) is secure
    response = Response()
    clear_auth_cookies(response, auth_settings)
    for value in response.headers.getlist("set-cookie"):
        deleted = SimpleCookie(value)
        for item in deleted.values():
            assert item["max-age"] == "0"
            assert item["path"] == "/"
            assert item["domain"] == ""
