"""Booking windows follow server time and the stored business policy."""

from datetime import UTC, date, datetime

import pytest

from app.domain.availability import AvailabilityConfigurationError, AvailabilityPolicy
from app.services import availability_rules


@pytest.mark.parametrize(
    "instant, timezone, horizon, expected",
    [
        (
            "2026-10-15T16:59:59+00:00",
            "Asia/Ho_Chi_Minh",
            7,
            (date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 22)),
        ),
        (
            "2026-10-15T17:00:00+00:00",
            "Asia/Ho_Chi_Minh",
            7,
            (date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 23)),
        ),
        (
            "2026-01-01T01:00:00+00:00",
            "America/Los_Angeles",
            3,
            (date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 3)),
        ),
    ],
)
def test_booking_window_uses_business_date_and_inclusive_policy_horizon(
    instant, timezone, horizon, expected
):
    assert (
        availability_rules.booking_date_window(
            now=datetime.fromisoformat(instant),
            policy=AvailabilityPolicy(timezone, 15, horizon),
        )
        == expected
    )


@pytest.mark.parametrize("now", [datetime(2026, 10, 15), datetime.max.replace(tzinfo=UTC)])
def test_booking_window_rejects_invalid_or_unrepresentable_clock(now):
    with pytest.raises(AvailabilityConfigurationError):
        availability_rules.booking_date_window(now=now, policy=AvailabilityPolicy("UTC", 15, 7))


@pytest.mark.parametrize(
    "policy",
    [
        AvailabilityPolicy("secret-invalid-zone", 15, 7),
        AvailabilityPolicy("UTC", 0, 7),
        AvailabilityPolicy("UTC", 15, 0),
    ],
)
def test_booking_window_validates_every_stored_policy_field(policy):
    with pytest.raises(AvailabilityConfigurationError):
        availability_rules.booking_date_window(
            now=datetime(2026, 10, 15, tzinfo=UTC), policy=policy
        )
