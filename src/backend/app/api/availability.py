"""Student-only availability preview endpoint."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_responses
from app.dependencies.catalog import reject_duplicate_catalog_query, require_student
from app.dependencies.clock import get_clock
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.availability import AvailabilityQuery, AvailabilityResponse
from app.services import availability as availability_service

router = APIRouter(tags=["availability"])


@router.get(
    "/availability",
    response_model=AvailabilityResponse,
    responses=catalog_responses(200, 401, 403, 404, 422, 500, 503),
)
async def availability(
    request: Request,
    query: Annotated[AvailabilityQuery, Query()],
    context: Annotated[AuthContext, Depends(require_student)],
    _query_guard: Annotated[None, Depends(reject_duplicate_catalog_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AvailabilityResponse:
    del _query_guard
    return await availability_service.get_availability(
        db,
        query,
        student_id=context.user_id,
        settings=request.app.state.settings,
        clock=clock,
    )
