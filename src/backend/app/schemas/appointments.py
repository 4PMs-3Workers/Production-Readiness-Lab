"""Strict public appointment request and immutable creation receipt schemas."""

from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.schemas.common import PaginationParams, UtcTimestamp


def _reject_blank_key(value: str) -> str:
    if not value.strip():
        raise ValueError("Idempotency-Key must not be blank")
    return value


IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"\S", strip_whitespace=False),
    AfterValidator(_reject_blank_key),
]
AppointmentStatus = Literal["UPCOMING", "CHANGE_PENDING", "CANCELLED", "COMPLETED", "NO_SHOW"]


class AppointmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose_id: UUID
    staff_id: UUID
    starts_at: AwareDatetime
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("starts_at")
    @classmethod
    def require_utc_representable_timestamp(cls, value: AwareDatetime) -> AwareDatetime:
        try:
            value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("starts_at must be representable in UTC") from error
        return value


class AppointmentCreateResponse(BaseModel):
    """Original creation receipt returned on both create and idempotent replay."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: Literal["UPCOMING"]
    created_at: AwareDatetime


class AppointmentListQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    status: AppointmentStatus | None = None


class AppointmentListItem(BaseModel):
    """Student-visible history fields, excluding private persistence metadata."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    purpose_id: UUID
    purpose_code: str
    purpose_name: str
    staff_id: UUID
    staff_name: str
    duration_minutes: int
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp
    location: str | None
    status: AppointmentStatus
    created_at: UtcTimestamp


class AppointmentDetailResponse(AppointmentListItem):
    """Current owned detail including description and cancellation information."""

    description: str | None
    cancelled_at: UtcTimestamp | None
    cancel_reason: str | None
