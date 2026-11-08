"""The whole school policy and weekly schedule form one versioned resource."""

import re
from datetime import datetime, time
from importlib.resources import files
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from app.schemas.common import PositiveSmallInt, RevisionTimestamp

WALL_TIME = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"
LOCATION_ZONES = frozenset(
    line.split("\t")[2]
    for line in files("tzdata.zoneinfo")
    .joinpath("zone.tab")
    .read_text(encoding="utf-8")
    .splitlines()
    if line and not line.startswith("#")
)
PORTABLE_ZONES = LOCATION_ZONES | {"UTC", "Etc/UTC"}


def parse_wall_time(value: object) -> time:
    if isinstance(value, str) and re.fullmatch(WALL_TIME, value):
        return time.fromisoformat(value)
    if isinstance(value, time) and value.tzinfo is None and value.microsecond == 0:
        return value
    raise ValueError("Service hours must be local times in HH:MM:SS")


WallTime = Annotated[
    time,
    BeforeValidator(parse_wall_time),
    WithJsonSchema({"type": "string", "pattern": WALL_TIME}, mode="validation"),
]


class WeeklyWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    weekday: Annotated[int, Field(strict=True, ge=1, le=7)]
    opens_at: WallTime
    closes_at: WallTime

    @model_validator(mode="after")
    def positive_interval(self) -> "WeeklyWindow":
        if self.opens_at >= self.closes_at:
            raise ValueError("A service window must end later on the same day")
        return self


class BookingConfiguration(BaseModel):
    timezone: str
    start_step_minutes: int
    booking_horizon_days: int
    windows: list[WeeklyWindow]
    updated_at: datetime


class BookingConfigurationReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: RevisionTimestamp
    timezone: str = Field(min_length=1, max_length=64)
    start_step_minutes: PositiveSmallInt
    booking_horizon_days: PositiveSmallInt
    windows: list[WeeklyWindow]

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, value: str) -> str:
        if value not in PORTABLE_ZONES:
            raise ValueError("Use an IANA location timezone or UTC")
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError, OSError):
            raise ValueError("Unknown IANA timezone") from None
        return value

    @model_validator(mode="after")
    def normalize_windows(self) -> "BookingConfigurationReplace":
        self.windows.sort(key=lambda item: (item.weekday, item.opens_at, item.closes_at))
        for previous, current in zip(self.windows, self.windows[1:], strict=False):
            if previous.weekday == current.weekday and current.opens_at < previous.closes_at:
                raise ValueError("Service windows on the same day must not overlap")
        return self
