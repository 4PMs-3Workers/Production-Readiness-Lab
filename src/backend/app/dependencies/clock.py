"""Injectable wall-clock source for time-dependent request services."""

from collections.abc import Callable
from datetime import datetime

from app.models.base import utc_now


def get_clock() -> Callable[[], datetime]:
    """Return the current UTC clock without sampling it during dependency setup."""
    return utc_now
