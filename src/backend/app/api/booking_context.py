"""Student-only authoritative booking calendar endpoint."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_error, catalog_responses
from app.dependencies.catalog import require_student
from app.dependencies.clock import get_clock
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.booking_context import BookingContextResponse
from app.services import booking_context as booking_context_service

router = APIRouter(tags=["booking-context"])


@router.get(
    "/booking-context",
    response_model=BookingContextResponse,
    responses=catalog_responses(200, 401, 403, 422, 500, 503),
)
async def booking_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_student)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> BookingContextResponse:
    del context
    if request.query_params:
        raise catalog_error("VALIDATION_ERROR")
    return await booking_context_service.get_booking_context(
        db, settings=request.app.state.settings, clock=clock
    )
