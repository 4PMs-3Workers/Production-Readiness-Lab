"""Authenticated staff business-time context endpoint."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_responses
from app.dependencies.clock import get_clock
from app.dependencies.database import get_db_session
from app.dependencies.staff import reject_staff_detail_query, require_staff
from app.domain.auth import AuthContext
from app.schemas.staff_context import StaffContextResponse
from app.services import staff_context as service

router = APIRouter(prefix="/staff/me", tags=["staff context"])


@router.get(
    "/context",
    response_model=StaffContextResponse,
    responses=catalog_responses(200, 401, 403, 422, 500, 503),
    summary="Get the staff business calendar context",
)
async def staff_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_staff)],
    _query: Annotated[None, Depends(reject_staff_detail_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> StaffContextResponse:
    del context, _query
    return await service.get_staff_context(db, settings=request.app.state.settings, clock=clock)
