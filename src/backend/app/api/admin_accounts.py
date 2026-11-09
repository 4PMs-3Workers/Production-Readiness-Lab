"""ADMIN-only account administration routes."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorResponse
from app.core.request_context import get_request_id
from app.dependencies.auth import check_csrf, require_roles
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.admin_accounts import (
    AccountCreateRequest,
    AccountItem,
    AccountQuery,
    AccountUpdateRequest,
    PasswordResetRequest,
)
from app.schemas.common import PageResponse
from app.services import admin_accounts as account_service

router = APIRouter(prefix="/admin/accounts", tags=["admin accounts"])

ACCOUNT_WRITE_OPENAPI = {
    "parameters": [
        {
            "name": "X-CSRF-Token",
            "in": "header",
            "required": True,
            "description": "Signed token matching the prl_csrf cookie.",
            "schema": {"type": "string"},
        },
        {
            "name": "Origin",
            "in": "header",
            "required": False,
            "description": (
                "Preferred same-origin header. If present, it takes precedence over Referer "
                "and must match the configured application origin."
            ),
            "schema": {"type": "string", "format": "uri"},
        },
        {
            "name": "Referer",
            "in": "header",
            "required": False,
            "description": (
                "Accepted only when Origin is absent; its origin must match the configured "
                "application origin."
            ),
            "schema": {"type": "string", "format": "uri"},
        },
    ]
}
ACCOUNT_WRITE_DESCRIPTION = (
    "This mutation requires the signed CSRF token in `X-CSRF-Token`. Origin or Referer "
    "must match the configured application origin. Origin takes precedence when both are supplied."
)


def account_responses(
    success: int, *statuses: int, description: str = "Sanitized account-management error"
) -> dict[int | str, dict[str, Any]]:
    headers = {
        "X-Request-ID": {"schema": {"type": "string"}},
        "Cache-Control": {"schema": {"type": "string"}},
    }
    responses: dict[int | str, dict[str, Any]] = {success: {"headers": headers}}
    for status in statuses:
        responses[status] = {
            "model": ErrorResponse,
            "headers": headers | {"X-Error-Class": {"schema": {"type": "string"}}},
            "description": description,
        }
    return responses


@router.get(
    "",
    response_model=PageResponse[AccountItem],
    responses=account_responses(200, 401, 403, 413, 415, 422, 500, 503),
)
async def list_accounts(
    request: Request,
    query: Annotated[AccountQuery, Query()],
    context: Annotated[AuthContext, Depends(require_roles("ADMIN"))],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[AccountItem]:
    """List accounts by role, active state, or an email/name substring."""
    return await account_service.list_accounts(
        db, query, context=context, settings=request.app.state.settings
    )


@router.post(
    "",
    status_code=201,
    response_model=AccountItem,
    responses=account_responses(201, 401, 403, 409, 413, 415, 422, 500, 503),
    description=(
        "Create an internal account; the submitted password is never returned or audited. "
        + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def create_account(
    payload: AccountCreateRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_roles("ADMIN"))],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountItem:
    """Create an internal account; the submitted password is never returned or audited."""
    check_csrf(request, context)
    return await account_service.create_account(
        db,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
        passwords=request.app.state.passwords,
    )


@router.patch(
    "/{account_id}",
    response_model=AccountItem,
    responses=account_responses(200, 401, 403, 404, 409, 413, 415, 422, 500, 503),
    description=(
        "Update an account using its current `updated_at` as a stale-write precondition. "
        + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def update_account(
    account_id: UUID,
    payload: AccountUpdateRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_roles("ADMIN"))],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountItem:
    """Update an account using its current `updated_at` as a stale-write precondition."""
    check_csrf(request, context)
    return await account_service.update_account(
        db,
        account_id,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
    )


@router.post(
    "/{account_id}/password",
    response_model=AccountItem,
    responses=account_responses(200, 401, 403, 404, 409, 413, 415, 422, 500, 503),
    description=(
        "Set a new internal password and revoke every existing session atomically. "
        + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def reset_password(
    account_id: UUID,
    payload: PasswordResetRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_roles("ADMIN"))],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountItem:
    """Set a new internal password and revoke every existing session atomically."""
    check_csrf(request, context)
    return await account_service.reset_password(
        db,
        account_id,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
        passwords=request.app.state.passwords,
    )
