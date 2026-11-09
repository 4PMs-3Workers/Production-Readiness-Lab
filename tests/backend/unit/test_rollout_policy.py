from datetime import date

import pytest
from tools.deploy.rollout_policy import compatible_policy


@pytest.mark.parametrize(
    ("zone", "opening", "closing", "expected"),
    [
        ("America/New_York", "02:30:00", "03:30:00", False),
        ("America/New_York", "01:30:00", "02:30:00", False),
        ("America/Havana", "09:00:00", "12:00:00", False),
        ("America/New_York", "09:00:00", "12:00:00", True),
    ],
)
def test_predecessor_policy_checks_recurring_boundaries_beyond_the_current_booking_horizon(
    zone, opening, closing, expected
):
    assert (
        compatible_policy(
            {
                "timezone": zone,
                "start_step_minutes": 15,
                "booking_horizon_days": 7,
                "windows": {"sunday": [7, opening, closing]},
            },
            today=date(2027, 2, 13),
        )
        is expected
    )
