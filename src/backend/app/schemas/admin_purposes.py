"""Administrative purpose contracts including inactive catalog entries."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from app.schemas.common import PaginationParams

PositiveSmallInt = Annotated[int, Field(strict=True, ge=1, le=32767)]


class AdminPurposeQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None


class PurposeValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    duration_minutes: PositiveSmallInt
    requires_free_text: StrictBool
    is_active: StrictBool


class PurposeCreate(PurposeValues):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,49}$")]


class PurposeReplace(PurposeValues):
    expected_updated_at: AwareDatetime


class AdminPurpose(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    duration_minutes: int
    requires_free_text: bool
    is_active: bool
    updated_at: datetime
