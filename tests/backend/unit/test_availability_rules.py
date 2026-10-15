from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
    ServiceWindow,
    TimeInterval,
)
from app.services.availability_rules import (
    business_day_bounds,
    generate_slots,
    validate_booking_date,
)

NOW = datetime.fromisoformat("2026-10-15T12:00:00+00:00")
POLICY = AvailabilityPolicy("Asia/Ho_Chi_Minh", 15, 7)
SELECTED_DATE = date(2026, 10, 16)
BASELINE_WINDOW = ServiceWindow(time(8, 10), time(9, 10))


def starts(slots: Sequence[TimeInterval]) -> list[str]:
    return [slot.starts_at.isoformat() for slot in slots]


def test_starts_are_anchored_to_window_opening():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:25:00+00:00",
        "2026-10-16T01:40:00+00:00",
    ]
    assert slots[-1].ends_at == datetime(2026, 10, 16, 2, 10, tzinfo=UTC)


def test_policy_overrides_step():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=AvailabilityPolicy("Asia/Ho_Chi_Minh", 20, 7),
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:30:00+00:00",
    ]


def test_duration_override():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=40,
        windows=[BASELINE_WINDOW],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:25:00+00:00",
    ]


def test_window_seconds_are_preserved():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[ServiceWindow(time(8, 10, 30), time(9, 10, 30))],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:30+00:00",
        "2026-10-16T01:25:30+00:00",
        "2026-10-16T01:40:30+00:00",
    ]


def test_single_window_fit_does_not_merge_adjacent_windows():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=45,
        windows=[
            ServiceWindow(time(8, 10), time(8, 40)),
            ServiceWindow(time(8, 40), time(9, 10)),
        ],
        busy=[],
    )
    assert starts(slots) == []


def test_adjacent_windows_are_evaluated_independently():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[
            ServiceWindow(time(8, 10), time(8, 40)),
            ServiceWindow(time(8, 40), time(9, 10)),
        ],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:40:00+00:00",
    ]


def test_break_starts_a_new_window_grid():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW, ServiceWindow(time(9, 20), time(10, 0))],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:25:00+00:00",
        "2026-10-16T01:40:00+00:00",
        "2026-10-16T02:20:00+00:00",
    ]


@pytest.mark.parametrize(
    ("windows", "duration_minutes"),
    [([], 30), ([ServiceWindow(time(8, 10), time(8, 20))], 30)],
)
def test_empty_or_short_windows_return_no_slots(windows, duration_minutes):
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=duration_minutes,
        windows=windows,
        busy=[],
    )
    assert starts(slots) == []


def test_equal_duration_and_large_step_produces_opening_once():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=AvailabilityPolicy("Asia/Ho_Chi_Minh", 32767, 7),
        duration_minutes=30,
        windows=[ServiceWindow(time(8, 10), time(8, 40))],
        busy=[],
    )
    assert starts(slots) == ["2026-10-16T01:10:00+00:00"]


@pytest.mark.parametrize(
    ("busy", "expected"),
    [
        pytest.param(
            TimeInterval(
                datetime(2026, 10, 16, 1, 10, tzinfo=UTC),
                datetime(2026, 10, 16, 1, 40, tzinfo=UTC),
            ),
            ["2026-10-16T01:40:00+00:00"],
            id="equal",
        ),
        pytest.param(
            TimeInterval(
                datetime(2026, 10, 16, 1, 0, tzinfo=UTC),
                datetime(2026, 10, 16, 2, 0, tzinfo=UTC),
            ),
            [],
            id="contains-slot",
        ),
        pytest.param(
            TimeInterval(
                datetime(2026, 10, 16, 1, 20, tzinfo=UTC),
                datetime(2026, 10, 16, 1, 30, tzinfo=UTC),
            ),
            ["2026-10-16T01:40:00+00:00"],
            id="inside-slot-and-left-overlap",
        ),
        pytest.param(
            TimeInterval(
                datetime(2026, 10, 16, 1, 50, tzinfo=UTC),
                datetime(2026, 10, 16, 2, 0, tzinfo=UTC),
            ),
            ["2026-10-16T01:10:00+00:00"],
            id="right-overlap",
        ),
    ],
)
def test_overlap_shapes_remove_intersecting_slots(busy, expected):
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[busy],
    )
    assert starts(slots) == expected


def test_half_open_overlap_removes_both_intersecting_slots():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[
            TimeInterval(
                datetime(2026, 10, 16, 1, 40, tzinfo=UTC),
                datetime(2026, 10, 16, 1, 55, tzinfo=UTC),
            )
        ],
    )
    assert starts(slots) == ["2026-10-16T01:10:00+00:00"]


def test_microsecond_overlap_keeps_slot_starting_at_busy_end():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[
            TimeInterval(
                datetime(2026, 10, 16, 1, 39, 59, 999999, tzinfo=UTC),
                datetime(2026, 10, 16, 1, 40, tzinfo=UTC),
            )
        ],
    )
    assert starts(slots) == ["2026-10-16T01:40:00+00:00"]


@pytest.mark.parametrize(
    "busy",
    [
        TimeInterval(
            datetime(2026, 10, 16, 1, 0, tzinfo=UTC),
            datetime(2026, 10, 16, 1, 10, tzinfo=UTC),
        ),
        TimeInterval(
            datetime(2026, 10, 16, 2, 10, tzinfo=UTC),
            datetime(2026, 10, 16, 2, 20, tzinfo=UTC),
        ),
    ],
)
def test_external_adjacency_does_not_overlap(busy):
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[busy],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:25:00+00:00",
        "2026-10-16T01:40:00+00:00",
    ]


def test_results_are_sorted_and_unique_for_reversed_duplicate_inputs():
    slots = generate_slots(
        selected_date=SELECTED_DATE,
        now=NOW,
        policy=POLICY,
        duration_minutes=30,
        windows=[
            ServiceWindow(time(9, 20), time(10, 20)),
            BASELINE_WINDOW,
            ServiceWindow(time(9, 20), time(10, 20)),
            BASELINE_WINDOW,
        ],
        busy=[
            TimeInterval(
                datetime(2026, 10, 16, 2, 35, tzinfo=UTC),
                datetime(2026, 10, 16, 2, 40, tzinfo=UTC),
            ),
            TimeInterval(
                datetime(2026, 10, 16, 1, 0, tzinfo=UTC),
                datetime(2026, 10, 16, 1, 5, tzinfo=UTC),
            ),
            TimeInterval(
                datetime(2026, 10, 16, 2, 35, tzinfo=UTC),
                datetime(2026, 10, 16, 2, 40, tzinfo=UTC),
            ),
        ],
    )
    assert starts(slots) == [
        "2026-10-16T01:10:00+00:00",
        "2026-10-16T01:25:00+00:00",
        "2026-10-16T01:40:00+00:00",
        "2026-10-16T02:50:00+00:00",
    ]


@pytest.mark.parametrize(
    "windows",
    [
        [ServiceWindow(time(9, 10), time(8, 10))],
        [ServiceWindow(time(8, 10), time(8, 50)), ServiceWindow(time(8, 40), time(9, 10))],
    ],
)
def test_invalid_windows_raise_configuration_error(windows):
    with pytest.raises(AvailabilityConfigurationError):
        generate_slots(
            selected_date=SELECTED_DATE,
            now=NOW,
            policy=POLICY,
            duration_minutes=30,
            windows=windows,
            busy=[],
        )


@pytest.mark.parametrize(
    ("selected_date", "horizon", "valid"),
    [
        (date(2026, 10, 16), 7, True),
        (date(2026, 10, 22), 7, True),
        (date(2026, 10, 15), 7, False),
        (date(2026, 10, 23), 7, False),
        (date(2026, 10, 16), 1, True),
        (date(2026, 10, 17), 1, False),
        (date(2026, 10, 16), 32767, True),
    ],
)
def test_horizon_accepts_only_selected_date_inclusive_window(selected_date, horizon, valid):
    policy = AvailabilityPolicy("Asia/Ho_Chi_Minh", 15, horizon)
    if valid:
        validate_booking_date(selected_date=selected_date, now=NOW, policy=policy)
        slots = generate_slots(
            selected_date=selected_date,
            now=NOW,
            policy=policy,
            duration_minutes=30,
            windows=[BASELINE_WINDOW],
            busy=[],
        )
        assert starts(slots) == [
            f"{selected_date.isoformat()}T01:10:00+00:00",
            f"{selected_date.isoformat()}T01:25:00+00:00",
            f"{selected_date.isoformat()}T01:40:00+00:00",
        ]
    else:
        with pytest.raises(BookingDateError):
            validate_booking_date(selected_date=selected_date, now=NOW, policy=policy)


@pytest.mark.parametrize(
    ("now", "selected_date", "valid"),
    [
        (datetime.fromisoformat("2026-10-15T16:59:59.999999+00:00"), date(2026, 10, 16), True),
        (datetime.fromisoformat("2026-10-15T17:00:00+00:00"), date(2026, 10, 16), False),
    ],
)
def test_local_midnight_changes_business_date_at_timezone_boundary(now, selected_date, valid):
    if valid:
        validate_booking_date(selected_date=selected_date, now=now, policy=POLICY)
    else:
        with pytest.raises(BookingDateError):
            validate_booking_date(selected_date=selected_date, now=now, policy=POLICY)


def test_calendar_rollover_and_leap_day_bounds():
    utc = ZoneInfo("UTC")
    new_year = business_day_bounds(selected_date=date(2027, 1, 1), zone=utc)
    leap_day = business_day_bounds(selected_date=date(2028, 2, 29), zone=utc)
    assert new_year.starts_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert new_year.ends_at == datetime(2027, 1, 2, tzinfo=UTC)
    assert leap_day.starts_at == datetime(2028, 2, 29, tzinfo=UTC)
    assert leap_day.ends_at == datetime(2028, 3, 1, tzinfo=UTC)
    assert date(2027, 1, 3).isoweekday() == 7
    assert date(2027, 1, 4).isoweekday() == 1


def test_timezone_is_read_from_policy_not_assumed_to_be_utc_plus_seven():
    now = datetime.fromisoformat("2026-10-15T18:15:00+00:00")
    policy = AvailabilityPolicy("Asia/Kathmandu", 15, 7)
    slots = generate_slots(
        selected_date=date(2026, 10, 17),
        now=now,
        policy=policy,
        duration_minutes=30,
        windows=[BASELINE_WINDOW],
        busy=[],
    )
    assert starts(slots) == [
        "2026-10-17T02:25:00+00:00",
        "2026-10-17T02:40:00+00:00",
        "2026-10-17T02:55:00+00:00",
    ]


@pytest.mark.parametrize(
    ("policy", "duration_minutes", "now", "busy"),
    [
        (AvailabilityPolicy("Not/AZone", 15, 7), 30, NOW, []),
        (AvailabilityPolicy("Asia/Ho_Chi_Minh", 0, 7), 30, NOW, []),
        (AvailabilityPolicy("Asia/Ho_Chi_Minh", 15, 0), 30, NOW, []),
        (AvailabilityPolicy("Asia/Ho_Chi_Minh", 10**100, 7), 30, NOW, []),
        (POLICY, 0, NOW, []),
        (POLICY, 10**100, NOW, []),
        (POLICY, 30, datetime(2026, 10, 15, 12), []),
        (POLICY, 30, NOW, [TimeInterval(datetime(2026, 10, 16, 1), datetime(2026, 10, 16, 2))]),
        (
            POLICY,
            30,
            NOW,
            [
                TimeInterval(
                    datetime(2026, 10, 16, 2, tzinfo=UTC),
                    datetime(2026, 10, 16, 1, tzinfo=UTC),
                )
            ],
        ),
    ],
)
def test_invalid_policy_duration_clock_or_busy_interval_raises_configuration_error(
    policy, duration_minutes, now, busy
):
    with pytest.raises(AvailabilityConfigurationError):
        generate_slots(
            selected_date=SELECTED_DATE,
            now=now,
            policy=policy,
            duration_minutes=duration_minutes,
            windows=[BASELINE_WINDOW],
            busy=busy,
        )


def test_date_at_maximum_year_raises_booking_date_error_not_overflow():
    now = datetime(9999, 12, 30, 12, tzinfo=UTC)
    policy = AvailabilityPolicy("UTC", 15, 7)
    with pytest.raises(BookingDateError):
        generate_slots(
            selected_date=date(9999, 12, 31),
            now=now,
            policy=policy,
            duration_minutes=30,
            windows=[BASELINE_WINDOW],
            busy=[],
        )


@pytest.mark.parametrize(
    ("selected_date", "window", "expected"),
    [
        (
            date(2026, 3, 8),
            ServiceWindow(time(1, 30), time(3, 30)),
            ["2026-03-08T06:30:00+00:00", "2026-03-08T07:00:00+00:00"],
        ),
        (
            date(2026, 11, 1),
            ServiceWindow(time(0, 30), time(2, 30)),
            [
                "2026-11-01T04:30:00+00:00",
                "2026-11-01T05:00:00+00:00",
                "2026-11-01T05:30:00+00:00",
                "2026-11-01T06:00:00+00:00",
                "2026-11-01T06:30:00+00:00",
                "2026-11-01T07:00:00+00:00",
            ],
        ),
    ],
)
def test_dst_starts_skip_gaps_and_include_both_ambiguous_folds(selected_date, window, expected):
    zone = ZoneInfo("America/New_York")
    now = datetime.combine(selected_date - timedelta(days=1), time(12), tzinfo=zone)
    policy = AvailabilityPolicy("America/New_York", 30, 7)
    slots = generate_slots(
        selected_date=selected_date,
        now=now,
        policy=policy,
        duration_minutes=30,
        windows=[window],
        busy=[],
    )
    assert starts(slots) == expected


@pytest.mark.parametrize(
    ("selected_date", "window"),
    [
        (date(2026, 3, 8), ServiceWindow(time(2), time(4))),
        (date(2026, 11, 1), ServiceWindow(time(1), time(2))),
    ],
)
def test_nonexistent_or_ambiguous_window_boundary_is_configuration_error(selected_date, window):
    zone = ZoneInfo("America/New_York")
    now = datetime.combine(selected_date - timedelta(days=1), time(12), tzinfo=zone)
    with pytest.raises(AvailabilityConfigurationError):
        generate_slots(
            selected_date=selected_date,
            now=now,
            policy=AvailabilityPolicy("America/New_York", 30, 7),
            duration_minutes=30,
            windows=[window],
            busy=[],
        )


def test_busy_interval_order_is_validated_by_instant_across_dst_fold():
    zone = ZoneInfo("America/New_York")
    selected_date = date(2026, 11, 1)
    now = datetime.combine(selected_date - timedelta(days=1), time(12), tzinfo=zone)
    busy = TimeInterval(
        datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
        datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0),
    )
    with pytest.raises(AvailabilityConfigurationError):
        generate_slots(
            selected_date=selected_date,
            now=now,
            policy=AvailabilityPolicy("America/New_York", 30, 7),
            duration_minutes=30,
            windows=[ServiceWindow(time(0, 30), time(2, 30))],
            busy=[busy],
        )
