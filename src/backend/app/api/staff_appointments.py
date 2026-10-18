"""Staff-only appointment routes; services own reads and state transitions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import STUDENT_WRITE_OPENAPI, appointment_responses
from app.api.errors import ServiceUnavailable
from app.dependencies.auth import require_csrf
from app.dependencies.database import get_db_session
from app.dependencies.staff import (
    reject_duplicate_staff_query,
    reject_staff_write_query,
    require_staff,
)
from app.domain.auth import AuthContext
from app.schemas.appointments import AppointmentCancelRequest, AppointmentCancelResponse
from app.schemas.common import PageResponse
from app.schemas.staff_appointments import (
    StaffAppointmentDetailResponse,
    StaffAppointmentListItem,
    StaffAppointmentListQuery,
    StaffAppointmentOutcomeRequest,
    StaffAppointmentOutcomeResponse,
)

router = APIRouter(tags=["staff appointments"])


@router.get(
    "/staff/me/appointments",
    response_model=PageResponse[StaffAppointmentListItem],
    responses=appointment_responses(
        {200: PageResponse[StaffAppointmentListItem]}, 401, 403, 422, 500, 503
    ),
    summary="List appointments assigned to the current staff member",
)
async def list_staff_appointments(
    request: Request,
    query: Annotated[StaffAppointmentListQuery, Query()],
    context: Annotated[AuthContext, Depends(require_staff)],
    _query_guard: Annotated[None, Depends(reject_duplicate_staff_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[StaffAppointmentListItem]:
    del request, query, context, _query_guard, db
    raise ServiceUnavailable()


@router.get(
    "/staff/me/appointments/{appointment_id}",
    response_model=StaffAppointmentDetailResponse,
    responses=appointment_responses(
        {200: StaffAppointmentDetailResponse}, 401, 403, 404, 422, 500, 503
    ),
    summary="Get an appointment assigned to the current staff member",
)
async def get_staff_appointment(
    appointment_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffAppointmentDetailResponse:
    del appointment_id, request, context, db
    raise ServiceUnavailable()


@router.post(
    "/staff/me/appointments/{appointment_id}/cancel",
    response_model=AppointmentCancelResponse,
    responses=appointment_responses(
        {200: AppointmentCancelResponse}, 401, 403, 404, 409, 422, 500, 503
    ),
    summary="Cancel an appointment assigned to the current staff member",
    openapi_extra=STUDENT_WRITE_OPENAPI,
)
async def cancel_staff_appointment(
    appointment_id: UUID,
    request: Request,
    payload: AppointmentCancelRequest,
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf_context: Annotated[AuthContext, Depends(require_csrf)],
    _query_guard: Annotated[None, Depends(reject_staff_write_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AppointmentCancelResponse:
    del appointment_id, request, payload, context, _csrf_context, _query_guard, db
    raise ServiceUnavailable()


@router.post(
    "/staff/me/appointments/{appointment_id}/outcome",
    response_model=StaffAppointmentOutcomeResponse,
    responses=appointment_responses(
        {200: StaffAppointmentOutcomeResponse}, 401, 403, 404, 409, 422, 500, 503
    ),
    summary="Record the outcome of an appointment assigned to the current staff member",
    openapi_extra=STUDENT_WRITE_OPENAPI,
)
async def record_staff_appointment_outcome(
    appointment_id: UUID,
    request: Request,
    payload: StaffAppointmentOutcomeRequest,
    context: Annotated[AuthContext, Depends(require_staff)],
    _csrf_context: Annotated[AuthContext, Depends(require_csrf)],
    _query_guard: Annotated[None, Depends(reject_staff_write_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffAppointmentOutcomeResponse:
    del appointment_id, request, payload, context, _csrf_context, _query_guard, db
    raise ServiceUnavailable()
