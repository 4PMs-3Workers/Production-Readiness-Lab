"""Staff-owned preference and unavailability HTTP contracts."""

import asyncio
from copy import deepcopy
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import STUDENT_WRITE_OPENAPI, appointment_responses
from app.core.errors import AppError
from app.dependencies.auth import require_csrf
from app.dependencies.database import get_db_session
from app.dependencies.staff import (
    reject_duplicate_staff_query,
    reject_staff_write_query,
    require_staff,
)
from app.domain.auth import AuthContext
from app.schemas.common import PageResponse
from app.schemas.staff_availability import (
    StaffPurposeQuery,
    StaffPurposeRequest,
    StaffPurposeResponse,
    UnavailabilityCreateRequest,
    UnavailabilityQuery,
    UnavailabilityResponse,
)
from app.services import staff_availability as service

router = APIRouter(prefix="/staff/me", tags=["staff availability management"])
WRITE_OPENAPI = deepcopy(STUDENT_WRITE_OPENAPI)
WRITE_OPENAPI["parameters"][0][
    "description"
] = "Signed token matching the prl_csrf cookie; required for this staff configuration write."


def _deadline(request: Request) -> float:
    return getattr(request.state, "request_deadline", None) or (
        asyncio.get_running_loop().time() + request.app.state.settings.request_timeout_seconds
    )


async def _reject_delete_body(request: Request) -> None:
    if await request.body():
        raise AppError(422, "VALIDATION_ERROR", "Invalid request", "input")


@router.get(
    "/purposes",
    response_model=PageResponse[StaffPurposeResponse],
    responses=appointment_responses(
        {200: PageResponse[StaffPurposeResponse]}, 401, 403, 422, 500, 503
    ),
)
async def list_purposes(
    request: Request,
    query: Annotated[StaffPurposeQuery, Query()],
    context: Annotated[AuthContext, Depends(require_staff)],
    _query: Annotated[None, Depends(reject_duplicate_staff_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[StaffPurposeResponse]:
    """Active purposes and retained inactive associations, ordered by name then ID."""
    return await service.list_purposes(
        db, query, staff_id=context.user_id, settings=request.app.state.settings
    )


@router.put(
    "/purposes/{purpose_id}",
    response_model=StaffPurposeResponse,
    responses=appointment_responses(
        {200: StaffPurposeResponse}, 401, 403, 404, 409, 413, 422, 500, 503
    ),
    openapi_extra=WRITE_OPENAPI,
)
async def set_purpose(
    purpose_id: UUID,
    request: Request,
    payload: StaffPurposeRequest,
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    _query: Annotated[None, Depends(reject_staff_write_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffPurposeResponse:
    """Retain associations; an inactive purpose can only have an existing association disabled.

    An unchanged PUT preserves timestamps/audit count. Repeat the same PUT to recover from
    503 after an unknown commit; no automatic replay is attempted by the server.
    """
    return await service.set_purpose(
        db,
        purpose_id,
        payload,
        context=context,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )


@router.get(
    "/unavailability",
    response_model=PageResponse[UnavailabilityResponse],
    responses=appointment_responses(
        {200: PageResponse[UnavailabilityResponse]}, 401, 403, 422, 500, 503
    ),
)
async def list_periods(
    request: Request,
    query: Annotated[UnavailabilityQuery, Query()],
    context: Annotated[AuthContext, Depends(require_staff)],
    _query: Annotated[None, Depends(reject_duplicate_staff_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[UnavailabilityResponse]:
    """Owned periods overlapping the optional aware [from, to) interval; start then ID order.

    Either bound can be omitted. Both supplied require from < to. Refresh this list to
    recover from a DELETE whose commit outcome is unknown.
    """
    return await service.list_periods(
        db, query, staff_id=context.user_id, settings=request.app.state.settings
    )


@router.post(
    "/unavailability",
    response_model=UnavailabilityResponse,
    status_code=201,
    responses=appointment_responses(
        {200: UnavailabilityResponse, 201: UnavailabilityResponse},
        401,
        403,
        409,
        413,
        422,
        500,
        503,
    ),
    openapi_extra=WRITE_OPENAPI,
)
async def create_period(
    request: Request,
    response: Response,
    payload: UnavailabilityCreateRequest,
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    _query: Annotated[None, Depends(reject_staff_write_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UnavailabilityResponse:
    """Store a UTC interval, or return 409 UNAVAILABILITY_CONFLICT for occupying appointments.

    Adjacent appointments and overlapping absence periods are allowed. Exact normalized
    interval replay returns 200 before conflict checks, preserving ID/timestamp/audit count.
    Retry that exact interval to recover from a 503 with an unknown commit outcome.
    """
    result, replayed = await service.create_period(
        db,
        payload,
        context=context,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )
    response.status_code = 200 if replayed else 201
    return result


@router.delete(
    "/unavailability/{unavailability_id}",
    status_code=204,
    response_class=Response,
    responses=appointment_responses({204: None}, 401, 403, 404, 413, 422, 500, 503),
    openapi_extra=WRITE_OPENAPI,
)
async def delete_period(
    unavailability_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf: Annotated[AuthContext, Depends(require_csrf)],
    _query: Annotated[None, Depends(reject_staff_write_query)],
    _body: Annotated[None, Depends(_reject_delete_body)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Delete an owned period; missing/foreign/already-deleted IDs return 404 NOT_FOUND."""
    await service.delete_period(
        db,
        unavailability_id,
        context=context,
        request_id=request.state.request_id,
        settings=request.app.state.settings,
        deadline=_deadline(request),
    )
    return Response(status_code=204)
