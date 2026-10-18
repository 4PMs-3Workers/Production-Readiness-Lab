"""Student-only profile endpoint; identity always comes from the session."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_errors import catalog_error, catalog_responses
from app.dependencies.catalog import require_student
from app.dependencies.database import get_db_session
from app.domain.auth import AuthContext
from app.schemas.student_profile import StudentProfileResponse
from app.services import student_profile

router = APIRouter(tags=["student-profile"])


@router.get(
    "/students/me",
    response_model=StudentProfileResponse,
    responses=catalog_responses(200, 401, 403, 404, 422, 500, 503),
)
async def get_profile(
    request: Request,
    context: Annotated[AuthContext, Depends(require_student)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudentProfileResponse:
    if request.query_params:
        raise catalog_error("VALIDATION_ERROR")
    return await student_profile.get_student_profile(
        db, student_id=context.user_id, settings=request.app.state.settings
    )
