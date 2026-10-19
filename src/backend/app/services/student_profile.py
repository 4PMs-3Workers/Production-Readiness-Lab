"""Read only the current student's explicitly public profile fields."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.core.transactions import read_transaction
from app.repositories import users
from app.schemas.student_profile import StudentProfileResponse


async def get_student_profile(
    db: AsyncSession, *, student_id: UUID, settings: Settings
) -> StudentProfileResponse:
    async with read_transaction(db, settings):
        row = await users.find_student_by_id(db, student_id)
        if row is None:
            raise AppError(404, "NOT_FOUND", "Student profile was not found", "input")
        return StudentProfileResponse.model_validate(row, from_attributes=True)
