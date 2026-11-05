"""ADMIN-only account administration routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ErrorResponse
from app.core.request_context import get_request_id
from app.dependencies.auth import check_csrf, require_roles
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.admin_accounts import AccountCreateRequest, AccountItem, AccountQuery
from app.schemas.common import PageResponse
from app.services import admin_accounts as account_service

router = APIRouter(prefix="/admin/accounts", tags=["admin accounts"])


def account_responses(success: int, *statuses: int) -> dict[int | str, dict[str, Any]]:
    headers = {
        "X-Request-ID": {"schema": {"type": "string"}},
        "Cache-Control": {"schema": {"type": "string"}},
    }
    responses: dict[int | str, dict[str, Any]] = {success: {"headers": headers}}
    for status in statuses:
        responses[status] = {
            "model": ErrorResponse,
            "headers": headers | {"X-Error-Class": {"schema": {"type": "string"}}},
            "description": "Sanitized account-management error",
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
    return await account_service.list_accounts(
        db, query, context=context, settings=request.app.state.settings
    )


@router.post(
    "",
    status_code=201,
    response_model=AccountItem,
    responses=account_responses(201, 401, 403, 409, 413, 415, 422, 500, 503),
)
async def create_account(
    payload: AccountCreateRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_roles("ADMIN"))],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountItem:
    check_csrf(request, context)
    return await account_service.create_account(
        db,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
        passwords=request.app.state.passwords,
    )
