"""Staff-only appointment routes; services own reads and state transitions."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.appointments import STUDENT_WRITE_OPENAPI, appointment_responses
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
from app.services import cancellation, staff_appointments

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
    del _query_guard
    return await staff_appointments.list_staff_appointments(
        db, query, staff_id=context.user_id, settings=request.app.state.settings
    )


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
    return await staff_appointments.get_staff_appointment(
        db,
        appointment_id=appointment_id,
        staff_id=context.user_id,
        settings=request.app.state.settings,
    )


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
    del _csrf_context, _query_guard
    settings = request.app.state.settings
    deadline = getattr(request.state, "request_deadline", None)
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + settings.request_timeout_seconds
    return await cancellation.cancel_staff_appointment(
        db,
        appointment_id=appointment_id,
        payload=payload,
        context=context,
        request_id=getattr(request.state, "request_id", "unknown"),
        settings=settings,
        deadline=deadline,
    )


@router.post(
    "/staff/me/appointments/{appointment_id}/outcome",
    response_model=StaffAppointmentOutcomeResponse,
    responses=appointment_responses(
        {200: StaffAppointmentOutcomeResponse}, 401, 403, 404, 409, 422, 500, 503
    ),
    summary="Record the outcome of an appointment assigned to the current staff member",
    description=(
        "Only an assigned active staff member can record COMPLETED or NO_SHOW for an UPCOMING "
        "appointment. At or before ends_at the API returns 409 APPOINTMENT_NOT_ENDED; an "
        "incompatible state or contradictory retry returns 409 APPOINTMENT_STATE_CONFLICT."
    ),
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
    del _csrf_context, _query_guard
    settings = request.app.state.settings
    deadline = getattr(request.state, "request_deadline", None)
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + settings.request_timeout_seconds
    return await staff_appointments.record_staff_appointment_outcome(
        db,
        appointment_id=appointment_id,
        outcome=payload,
        context=context,
        request_id=getattr(request.state, "request_id", "unknown"),
        settings=settings,
        deadline=deadline,
    )
