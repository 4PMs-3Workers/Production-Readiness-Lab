"""Immutable values and errors for catalog availability calculations."""

from dataclasses import dataclass
from datetime import datetime, time


class AvailabilityConfigurationError(ValueError):
    """Raised when stored availability policy or schedule data is invalid."""


class BookingDateError(ValueError):
    """Raised when a requested date is outside the supported booking window."""


@dataclass(frozen=True)
class AvailabilityPolicy:
    timezone: str
    start_step_minutes: int
    booking_horizon_days: int


@dataclass(frozen=True)
class ServiceWindow:
    opens_at: time
    closes_at: time


@dataclass(frozen=True)
class TimeInterval:
    starts_at: datetime
    ends_at: datetime
