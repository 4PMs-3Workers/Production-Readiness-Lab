"""Schemas shared by current health routes and future business routers."""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, Field, RootModel, field_serializer


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class UtcTimestamp(RootModel[AwareDatetime]):
    @field_serializer("root", when_used="json")
    def serialize_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
