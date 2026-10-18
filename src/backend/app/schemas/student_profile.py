"""Read-only profile of the authenticated student."""

from typing import Literal

from app.schemas.auth import PublicUser


class StudentProfileResponse(PublicUser):
    student_code: str | None
    faculty: str | None
    major: str | None
    gender: Literal["MALE", "FEMALE", "OTHER", "UNDISCLOSED"] | None
