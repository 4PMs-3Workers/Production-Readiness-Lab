"""Reusable session, role, and CSRF dependencies; ownership remains in services."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_error
from app.core.auth_cookies import CSRF_COOKIE, SESSION_COOKIE
from app.core.csrf import verify_csrf_token
from app.core.origin import require_same_origin
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.auth import Role
from app.services import auth

session_cookie = APIKeyCookie(name=SESSION_COOKIE, scheme_name="prl_session", auto_error=False)


def unique_cookie(request: Request, name: str, code: str) -> str | None:
    matches = []
    for value in request.headers.getlist("cookie"):
        for pair in value.split(";"):
            key, separator, token = pair.strip().partition("=")
            if key == name and separator:
                matches.append(token)
    if len(matches) > 1:
        raise auth_error(code)
    return matches[0] if matches else None


async def optional_auth(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    _token: Annotated[str | None, Security(session_cookie)],
) -> AuthContext | None:
    token = unique_cookie(request, SESSION_COOKIE, "INVALID_SESSION")
    return await auth.authenticate(db, token, settings=request.app.state.settings)


async def require_auth(
    context: Annotated[AuthContext | None, Depends(optional_auth)],
) -> AuthContext:
    if context is None:
        raise auth_error("INVALID_SESSION")
    return context


def require_roles(*allowed: Role) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    if not allowed:
        raise ValueError("At least one allowed role is required")

    async def check(context: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if context.role not in allowed:
            raise auth_error("FORBIDDEN_ROLE")
        return context

    return check


def check_csrf(request: Request, context: AuthContext) -> None:
    require_same_origin(request, request.app.state.settings)
    headers = request.headers.getlist("x-csrf-token")
    if len(headers) != 1:
        raise auth_error("INVALID_CSRF")
    verify_csrf_token(
        cookie=unique_cookie(request, CSRF_COOKIE, "INVALID_CSRF"),
        header=headers[0],
        session_id=context.session_id,
        secret=request.app.state.settings.csrf_secret.get_secret_value(),
    )


async def require_csrf(
    request: Request, context: Annotated[AuthContext, Depends(require_auth)]
) -> AuthContext:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_csrf(request, context)
    return context
