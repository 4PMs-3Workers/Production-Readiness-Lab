"""The versioned booking policy accepts only explicit positive quota integers."""

import pytest
from pydantic import ValidationError

from app.domain.booking import booking_quota_reached
from app.schemas.admin_booking_configuration import (
    BookingConfiguration,
    BookingConfigurationReplace,
)


def replace_payload(**changes):
    return {
        "expected_updated_at": "2026-11-01T00:00:00Z",
        "timezone": "Asia/Ho_Chi_Minh",
        "start_step_minutes": 15,
        "booking_horizon_days": 7,
        "max_active_appointments": 2,
        "windows": [],
        **changes,
    }


def test_policy_contract_round_trips_quota_as_integer():
    request = BookingConfigurationReplace.model_validate(replace_payload())
    response = BookingConfiguration.model_validate(
        {
            **replace_payload(),
            "updated_at": "2026-11-01T00:00:00Z",
        }
    )

    assert request.max_active_appointments == 2
    assert response.model_dump(mode="json")["max_active_appointments"] == 2


@pytest.mark.parametrize("value", [1, 32767])
def test_policy_contract_accepts_smallint_quota_boundaries(value):
    request = BookingConfigurationReplace.model_validate(
        replace_payload(max_active_appointments=value)
    )

    assert request.max_active_appointments == value


@pytest.mark.parametrize("value", [0, 32768, "2", True, None])
def test_policy_contract_rejects_non_positive_or_non_integer_quota(value):
    with pytest.raises(ValidationError):
        BookingConfigurationReplace.model_validate(replace_payload(max_active_appointments=value))


@pytest.mark.parametrize(
    ("active_count", "limit", "expected"),
    [(0, 1, False), (1, 1, True), (1, 2, False), (2, 2, True), (32767, 32767, True)],
)
def test_consumer_quota_boundary_matches_policy(active_count, limit, expected):
    assert booking_quota_reached(active_count, limit) is expected


@pytest.mark.parametrize(("active_count", "limit"), [(-1, 3), (0, 0), (0, 32768), (0, True)])
def test_consumer_rejects_invalid_quota_state(active_count, limit):
    with pytest.raises(ValueError):
        booking_quota_reached(active_count, limit)
