import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import UtcTimestamp


def require_ascii_iso_date(value: object) -> datetime.date | str:
    """Allow only a date object or the documented ASCII YYYY-MM-DD value."""
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str) and (
        len(value) == 10
        and value[4] == "-"
        and value[7] == "-"
        and all(char in "0123456789" for i, char in enumerate(value) if i not in (4, 7))
    ):
        return value
    raise ValueError("date must use YYYY-MM-DD format")


class AvailabilityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose_id: UUID
    staff_id: UUID
    date: datetime.date

    @field_validator("date", mode="before")
    @classmethod
    def require_ascii_iso_date(cls, value):
        return require_ascii_iso_date(value)


class AvailableSlot(BaseModel):
    starts_at: UtcTimestamp
    ends_at: UtcTimestamp


class AvailabilityResponse(BaseModel):
    date: datetime.date
    timezone: str
    slots: list[AvailableSlot]
