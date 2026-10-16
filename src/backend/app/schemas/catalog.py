from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.common import PaginationParams


class PurposeItem(BaseModel):
    id: UUID
    code: str
    name: str
    duration_minutes: int
    requires_free_text: bool


class StaffItem(BaseModel):
    id: UUID
    name: str


class PurposeQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")


class StaffQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    purpose_id: UUID
