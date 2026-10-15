"""One host-only policy for issuing and deleting authentication cookies."""

from datetime import datetime

from starlette.responses import Response

from app.core.config import Settings

SESSION_COOKIE = "prl_session"
CSRF_COOKIE = "prl_csrf"


def _set(
    response: Response,
    name: str,
    value: str,
    expires_at: datetime,
    now: datetime,
    settings: Settings,
    *,
    httponly: bool,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max(0, int((expires_at - now).total_seconds())),
        expires=expires_at,
        path="/",
        domain=None,
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite="lax",
    )


def set_csrf_cookie(
    response: Response, *, token: str, expires_at: datetime, now: datetime, settings: Settings
) -> None:
    _set(response, CSRF_COOKIE, token, expires_at, now, settings, httponly=False)


def set_auth_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    expires_at: datetime,
    now: datetime,
    settings: Settings,
) -> None:
    _set(response, SESSION_COOKIE, session_token, expires_at, now, settings, httponly=True)
    set_csrf_cookie(response, token=csrf_token, expires_at=expires_at, now=now, settings=settings)


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            domain=None,
            secure=settings.cookie_secure,
            httponly=name == SESSION_COOKIE,
            samesite="lax",
        )
