"""Student appointment creation and owned current-detail endpoints."""

import asyncio
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.dependencies.auth import require_csrf
from app.dependencies.catalog import require_student
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.repositories import appointments as appointment_repository
from app.schemas.appointments import (
    AppointmentCreateRequest,
    AppointmentCreateResponse,
    AppointmentDetailResponse,
    AppointmentStatus,
    IdempotencyKey,
)
from app.schemas.common import ErrorResponse
from app.services import booking as booking_service

router = APIRouter(tags=["appointments"])


def appointment_responses(
    success_models: dict[int, type[BaseModel]], *error_statuses: int
) -> dict[int, dict[str, Any]]:
    request_id = {"schema": {"type": "string"}, "description": "Identifier for this request"}
    cache_control = {
        "schema": {"type": "string"},
        "description": "Private responses are not stored",
    }
    success_headers = {"X-Request-ID": request_id, "Cache-Control": cache_control}
    error_headers = success_headers | {
        "X-Error-Class": {
            "schema": {"type": "string"},
            "description": "Classification of the sanitized error",
        }
    }
    responses: dict[int, dict[str, Any]] = {
        status: {"model": model, "headers": success_headers}
        for status, model in success_models.items()
    }
    for status in error_statuses:
        responses[status] = {
            "model": ErrorResponse,
            "headers": error_headers,
            "description": "Sanitized appointment error",
        }
    return responses


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Requested booking data was not found", "input")


@router.post(
    "/appointments",
    response_model=AppointmentCreateResponse,
    status_code=201,
    responses=appointment_responses(
        {200: AppointmentCreateResponse, 201: AppointmentCreateResponse},
        401,
        403,
        404,
        409,
        422,
        500,
        503,
    ),
    summary="Create a student appointment",
    openapi_extra={
        "parameters": [
            {
                "name": "X-CSRF-Token",
                "in": "header",
                "required": True,
                "description": "Signed token matching the prl_csrf cookie; required for this POST.",
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
    },
)
async def create_appointment(
    request: Request,
    response: Response,
    payload: AppointmentCreateRequest,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    context: Annotated[AuthContext, Depends(require_student)],
    _csrf_context: Annotated[AuthContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AppointmentCreateResponse:
    """Create a booking and replay a matching key's receipt.

    Origin or Referer must match the configured application origin. Origin takes precedence when
    both are sent. The X-CSRF-Token header must match the signed prl_csrf cookie.
    """
    del _csrf_context
    settings = request.app.state.settings
    deadline = getattr(request.state, "request_deadline", None)
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + settings.request_timeout_seconds
    receipt, replayed = await booking_service.create_appointment(
        db,
        payload,
        context=context,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
        settings=settings,
        deadline=deadline,
    )
    if replayed:
        response.status_code = 200
    return receipt


@router.get(
    "/appointments/{appointment_id}",
    response_model=AppointmentDetailResponse,
    responses=appointment_responses({200: AppointmentDetailResponse}, 401, 403, 404, 422, 500, 503),
    summary="Get an owned appointment",
)
async def get_appointment(
    appointment_id: UUID,
    context: Annotated[AuthContext, Depends(require_student)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AppointmentDetailResponse:
    """Return current booking details only for the authenticated student."""
    try:
        appointment = await appointment_repository.find_student_appointment(
            db, appointment_id=appointment_id, student_id=context.user_id
        )
    except SQLAlchemyError:
        raise ServiceUnavailable() from None
    if appointment is None:
        raise _not_found()
    return AppointmentDetailResponse(
        id=appointment.id,
        purpose_id=appointment.purpose_id,
        staff_id=appointment.staff_id,
        duration_minutes=appointment.duration_minutes,
        starts_at=appointment.starts_at,
        ends_at=appointment.ends_at,
        description=appointment.other_reason,
        location=appointment.location,
        status=cast(AppointmentStatus, appointment.status),
        created_at=appointment.created_at,
    )
