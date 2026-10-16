import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import UtcTimestamp


class AvailabilityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose_id: UUID
    staff_id: UUID
    date: datetime.date

    @field_validator("date", mode="before")
    @classmethod
    def require_ascii_iso_date(cls, value):
        if isinstance(value, str) and (
            len(value) != 10
            or value[4] != "-"
            or value[7] != "-"
            or any(char not in "0123456789-" for char in value)
        ):
            raise ValueError("date must use YYYY-MM-DD format")
        return value


class AvailableSlot(BaseModel):
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp


class AvailabilityResponse(BaseModel):
    date: datetime.date
    timezone: str
    slots: list[AvailableSlot]
