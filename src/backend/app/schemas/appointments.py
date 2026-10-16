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


def _reject_blank_key(value: str) -> str:
    if not value.strip():
        raise ValueError("Idempotency-Key must not be blank")
    return value


IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, strip_whitespace=False),
    AfterValidator(_reject_blank_key),
]


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
