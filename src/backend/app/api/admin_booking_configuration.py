"""ADMIN catalog and school-wide booking configuration HTTP endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_accounts import (
    ACCOUNT_WRITE_DESCRIPTION,
    ACCOUNT_WRITE_OPENAPI,
    account_responses,
)
from app.api.auth_errors import auth_error
from app.api.catalog_errors import catalog_error
from app.core.request_context import get_request_id
from app.dependencies.auth import check_csrf, require_roles
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.admin_booking_configuration import (
    BookingConfiguration,
    BookingConfigurationReplace,
)
from app.schemas.admin_purposes import (
    AdminPurpose,
    AdminPurposeQuery,
    PurposeCreate,
    PurposeReplace,
)
from app.schemas.common import PageResponse
from app.services import admin_booking_configuration as configuration
from app.services import admin_purposes as purposes

router = APIRouter(prefix="/admin", tags=["admin booking configuration"])
Admin = Annotated[AuthContext, Depends(require_roles("ADMIN"))]
Database = Annotated[AsyncSession, Depends(get_db_session)]


def _responses(success: int, *statuses: int):
    return account_responses(
        success, 401, 403, 422, 500, 503, *statuses, description="Sanitized configuration error"
    )


def _reject_query(request: Request) -> None:
    if request.query_params:
        raise catalog_error("VALIDATION_ERROR")


async def _require_write(request: Request, context: Admin) -> AuthContext:
    check_csrf(request, context)
    media_types = request.headers.getlist("content-type")
    if (
        len(media_types) != 1
        or media_types[0].split(";", 1)[0].strip().lower() != "application/json"
    ):
        raise auth_error("UNSUPPORTED_MEDIA_TYPE")
    _reject_query(request)
    return context


AdminWrite = Annotated[AuthContext, Depends(_require_write)]


@router.get("/purposes", response_model=PageResponse[AdminPurpose], responses=_responses(200))
async def list_purposes(
    request: Request, query: Annotated[AdminPurposeQuery, Query()], context: Admin, db: Database
) -> PageResponse[AdminPurpose]:
    """List active and inactive purposes in code order."""
    if len(request.query_params.multi_items()) != len(request.query_params):
        raise catalog_error("VALIDATION_ERROR")
    return await purposes.list_purposes(
        db, query, context=context, settings=request.app.state.settings
    )


@router.post(
    "/purposes",
    status_code=201,
    response_model=AdminPurpose,
    responses=_responses(201, 409, 413, 415),
    description=(
        "Create a purpose. A matching code and values return the existing purpose with 201. "
        + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def create_purpose(
    request: Request, payload: PurposeCreate, context: AdminWrite, db: Database
) -> AdminPurpose:
    return await purposes.create_purpose(
        db,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
    )


@router.get("/purposes/{purpose_id}", response_model=AdminPurpose, responses=_responses(200, 404))
async def get_purpose(
    purpose_id: UUID, request: Request, context: Admin, db: Database
) -> AdminPurpose:
    """Read one purpose, including a deactivated purpose."""
    _reject_query(request)
    return await purposes.get_purpose(
        db, purpose_id, context=context, settings=request.app.state.settings
    )


@router.put(
    "/purposes/{purpose_id}",
    response_model=AdminPurpose,
    responses=_responses(200, 404, 409, 413, 415),
    description=(
        "Replace mutable purpose fields, including activation, using expected_updated_at. "
        "The code and historical associations are retained. " + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def replace_purpose(
    purpose_id: UUID, request: Request, payload: PurposeReplace, context: AdminWrite, db: Database
) -> AdminPurpose:
    return await purposes.replace_purpose(
        db,
        purpose_id,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
    )


@router.get(
    "/booking-configuration", response_model=BookingConfiguration, responses=_responses(200)
)
async def get_configuration(request: Request, context: Admin, db: Database) -> BookingConfiguration:
    """Read school-wide policy and weekly hours at the same revision."""
    _reject_query(request)
    return await configuration.get_configuration(
        db, context=context, settings=request.app.state.settings
    )


@router.put(
    "/booking-configuration",
    response_model=BookingConfiguration,
    responses=_responses(200, 409, 413, 415),
    description=(
        "Atomically replace the policy and entire weekly schedule using expected_updated_at. "
        "Days omitted from windows are closed. " + ACCOUNT_WRITE_DESCRIPTION
    ),
    openapi_extra=ACCOUNT_WRITE_OPENAPI,
)
async def replace_configuration(
    request: Request, payload: BookingConfigurationReplace, context: AdminWrite, db: Database
) -> BookingConfiguration:
    return await configuration.replace_configuration(
        db,
        payload,
        context=context,
        settings=request.app.state.settings,
        request_id=get_request_id(request),
    )
