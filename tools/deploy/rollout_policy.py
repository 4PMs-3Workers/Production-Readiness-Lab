"""Check the recurring booking boundaries inside the reviewed predecessor image."""

import json
import signal
import sys
from datetime import UTC, date, datetime, time, timedelta

from app.domain.availability import AvailabilityPolicy
from app.services.availability_rules import _local_boundary_instants, validate_policy

GREGORIAN_CYCLE_DAYS = 146_097

POLICY_SQL = """
SELECT JSON_OBJECT(
    'timezone', timezone,
    'start_step_minutes', start_step_minutes,
    'booking_horizon_days', booking_horizon_days,
    'windows', COALESCE((
        SELECT JSON_OBJECTAGG(
            CONCAT_WS('/', weekday, opens_at, closes_at),
            JSON_ARRAY(weekday, CAST(opens_at AS CHAR), CAST(closes_at AS CHAR))
        ) FROM prl.service_hours
    ), JSON_OBJECT())
) FROM prl.service_settings WHERE id = 1;
"""


def compatible_policy(snapshot: dict, *, today: date | None = None) -> bool:
    policy = AvailabilityPolicy(
        snapshot["timezone"], snapshot["start_step_minutes"], snapshot["booking_horizon_days"]
    )
    zone = validate_policy(policy)
    first = today or datetime.now(UTC).astimezone(zone).date()
    weekday_boundaries = {day: {time.min} for day in range(1, 8)}
    for weekday, opening, closing in snapshot["windows"].values():
        weekday_boundaries[weekday].update(
            (time.fromisoformat(opening), time.fromisoformat(closing))
        )

    # Include today's midnight, the last bookable date's ending midnight, and
    # a full weekday/leap-year cycle beyond the current booking horizon.
    days = policy.booking_horizon_days + GREGORIAN_CYCLE_DAYS + 2
    for offset in range(days):
        day = first + timedelta(days=offset)
        if any(
            len(_local_boundary_instants(day, boundary, zone)) != 1
            for boundary in weekday_boundaries[day.isoweekday()]
        ):
            return False
    return True


if __name__ == "__main__":
    # Bound container work even if the Docker CLI stops waiting.
    if sys.platform != "win32":
        signal.alarm(45)
    raise SystemExit(0 if compatible_policy(json.load(sys.stdin)) else 1)
