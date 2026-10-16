"""Student-only read endpoints for purpose and staff catalogs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_responses
from app.dependencies.catalog import reject_duplicate_catalog_query, require_student
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.catalog import PurposeItem, PurposeQuery, StaffItem, StaffQuery
from app.schemas.common import PageResponse
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"])


@router.get(
    "/purposes",
    response_model=PageResponse[PurposeItem],
    responses=catalog_responses(200, 401, 403, 404, 422, 500, 503),
)
async def purposes(
    request: Request,
    query: Annotated[PurposeQuery, Query()],
    context: Annotated[AuthContext, Depends(require_student)],
    _query_guard: Annotated[None, Depends(reject_duplicate_catalog_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[PurposeItem]:
    del context, _query_guard
    return await catalog_service.list_purposes(db, query, settings=request.app.state.settings)


@router.get(
    "/staff",
    response_model=PageResponse[StaffItem],
    responses=catalog_responses(200, 401, 403, 404, 422, 500, 503),
)
async def staff(
    request: Request,
    query: Annotated[StaffQuery, Query()],
    context: Annotated[AuthContext, Depends(require_student)],
    _query_guard: Annotated[None, Depends(reject_duplicate_catalog_query)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PageResponse[StaffItem]:
    del context, _query_guard
    return await catalog_service.list_staff(db, query, settings=request.app.state.settings)
