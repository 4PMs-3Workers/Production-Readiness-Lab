"""Staff-owned consultation preferences and UTC absence intervals."""

from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from app.schemas.catalog import PurposeItem
from app.schemas.common import PaginationParams, UtcTimestamp


def _timestamp_input(value: object) -> object:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("An offset-bearing ISO timestamp is required") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An offset-bearing timestamp is required")
    return value


def _utc_timestamp(value: datetime) -> datetime:
    try:
        normalized = value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Timestamp must be representable in UTC") from exc
    if normalized.year < 1000:
        raise ValueError("Timestamp is outside the database datetime range")
    return normalized


OffsetTimestamp = Annotated[
    AwareDatetime, BeforeValidator(_timestamp_input), AfterValidator(_utc_timestamp)
]


class StaffPurposeQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")


class StaffPurposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_accepting: StrictBool


class StaffPurposeResponse(PurposeItem):
    model_config = ConfigDict(extra="forbid")

    is_active: bool
    is_accepting: bool


class UnavailabilityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: OffsetTimestamp
    ends_at: OffsetTimestamp

    @model_validator(mode="after")
    def require_positive_interval(self) -> Self:
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must precede ends_at")
        return self


class UnavailabilityQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    from_at: OffsetTimestamp | None = Field(default=None, alias="from")
    to_at: OffsetTimestamp | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def require_positive_range(self) -> Self:
        if self.from_at is not None and self.to_at is not None and self.from_at >= self.to_at:
            raise ValueError("from must precede to")
        return self


class UnavailabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp
    created_at: UtcTimestamp
