"""Thin session authentication routes; business services own persistence."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_errors import auth_responses
from app.core.auth_cookies import clear_auth_cookies, set_auth_cookies, set_csrf_cookie
from app.core.csrf import issue_csrf_token
from app.core.origin import require_same_origin
from app.dependencies.auth import check_csrf, optional_auth, require_auth
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.auth import CsrfResponse, LoginRequest, PublicUser
from app.services import auth

router = APIRouter(prefix="/auth", tags=["authentication"])


def public_user(context: AuthContext) -> PublicUser:
    return PublicUser(id=context.user_id, email=context.email, name=context.name, role=context.role)


@router.post(
    "/login",
    response_model=PublicUser,
    responses=auth_responses(200, 401, 403, 413, 415, 422, 429, 500, 503),
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicUser:
    """JSON credentials and valid Origin (or Referer fallback); no existing CSRF required.

    Issues fresh prl_session (HttpOnly) and prl_csrf cookies after commit.
    Other sessions remain active.
    """
    settings = request.app.state.settings
    require_same_origin(request, settings)
    result = await auth.login(
        db,
        credentials,
        settings=settings,
        passwords=request.app.state.passwords,
        throttle=request.app.state.login_throttle,
    )
    csrf = issue_csrf_token(result.context.session_id, settings.csrf_secret.get_secret_value())
    set_auth_cookies(
        response,
        session_token=result.session_token,
        csrf_token=csrf,
        expires_at=result.context.expires_at,
        now=datetime.now(UTC),
        settings=settings,
    )
    return public_user(result.context)


@router.get("/me", response_model=PublicUser, responses=auth_responses(200, 401, 500, 503))
async def me(context: Annotated[AuthContext, Depends(require_auth)]) -> PublicUser:
    return public_user(context)


@router.get("/csrf", response_model=CsrfResponse, responses=auth_responses(200, 401, 500, 503))
async def csrf(
    request: Request, response: Response, context: Annotated[AuthContext, Depends(require_auth)]
) -> CsrfResponse:
    settings = request.app.state.settings
    token = issue_csrf_token(context.session_id, settings.csrf_secret.get_secret_value())
    set_csrf_cookie(
        response,
        token=token,
        expires_at=context.expires_at,
        now=datetime.now(UTC),
        settings=settings,
    )
    return CsrfResponse(csrf_token=token, user_id=context.user_id)


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    responses=auth_responses(204, 401, 403, 422, 500, 503),
    openapi_extra={"security": [{"prl_session": []}, {}]},
)
async def logout(
    request: Request,
    context: Annotated[AuthContext | None, Depends(optional_auth)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    x_csrf_token: Annotated[
        str | None,
        Header(description="Signed token matching prl_csrf; required for a valid session"),
    ] = None,
) -> Response:
    """Origin or acceptable Referer is always required, including repeated logout.

    Valid sessions require matching signed prl_csrf cookie and X-CSRF-Token header.
    Missing/invalid sessions return 204 and clear cookies after origin validation.
    """
    settings = request.app.state.settings
    require_same_origin(request, settings)
    if context is not None:
        check_csrf(request, context)
    await auth.logout(db, context, settings=settings)
    response = Response(status_code=204)
    clear_auth_cookies(response, settings)
    return response
