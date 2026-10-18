"""Pure timezone-aware calculations for daily service availability."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
    ServiceWindow,
    TimeInterval,
)


def validate_policy(policy: AvailabilityPolicy) -> ZoneInfo:
    """Validate stored policy fields and return the configured IANA timezone."""
    if not isinstance(policy, AvailabilityPolicy):
        raise AvailabilityConfigurationError("Availability policy is invalid")
    if (
        not isinstance(policy.timezone, str)
        or not policy.timezone
        or not isinstance(policy.start_step_minutes, int)
        or isinstance(policy.start_step_minutes, bool)
        or policy.start_step_minutes <= 0
        or not isinstance(policy.booking_horizon_days, int)
        or isinstance(policy.booking_horizon_days, bool)
        or policy.booking_horizon_days <= 0
    ):
        raise AvailabilityConfigurationError("Availability policy is invalid")
    try:
        return ZoneInfo(policy.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AvailabilityConfigurationError("Availability timezone is invalid") from exc


def booking_date_window(*, now: datetime, policy: AvailabilityPolicy) -> tuple[date, date, date]:
    """Return the server business date and inclusive D+1 through D+horizon bounds."""
    zone = validate_policy(policy)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AvailabilityConfigurationError("Clock must return an aware datetime")
    try:
        business_date = now.astimezone(zone).date()
        return (
            business_date,
            business_date + timedelta(days=1),
            business_date + timedelta(days=policy.booking_horizon_days),
        )
    except (OverflowError, ValueError) as exc:
        raise AvailabilityConfigurationError("Booking window is out of range") from exc


def validate_booking_date(
    *, selected_date: date, now: datetime, policy: AvailabilityPolicy
) -> None:
    """Ensure a selected business date is D+1 through D+horizon inclusive."""
    if not isinstance(selected_date, date) or isinstance(selected_date, datetime):
        raise BookingDateError("Booking date is invalid")
    if selected_date == date.max:
        raise BookingDateError("Booking date has no representable next day")
    _, min_date, max_date = booking_date_window(now=now, policy=policy)
    if not min_date <= selected_date <= max_date:
        raise BookingDateError("Booking date is outside the configured horizon")


def _local_boundary_instants(
    local_date: date, wall_time: time, zone: ZoneInfo
) -> tuple[datetime, ...]:
    """Return valid UTC mappings for a local wall time, including both DST folds."""
    naive = datetime.combine(local_date, wall_time)
    instants: set[datetime] = set()
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            instants.add(instant)
    return tuple(sorted(instants))


def _unique_boundary(local_date: date, wall_time: time, zone: ZoneInfo) -> datetime:
    instants = _local_boundary_instants(local_date, wall_time, zone)
    if len(instants) != 1:
        raise AvailabilityConfigurationError(
            "Service and business-day boundaries must map to one instant"
        )
    return instants[0]


def business_day_bounds(*, selected_date: date, zone: ZoneInfo) -> TimeInterval:
    """Map local midnight boundaries independently into a UTC half-open interval."""
    if not isinstance(selected_date, date) or isinstance(selected_date, datetime):
        raise BookingDateError("Booking date is invalid")
    if not isinstance(zone, ZoneInfo):
        raise AvailabilityConfigurationError("Availability timezone is invalid")
    try:
        next_date = selected_date + timedelta(days=1)
    except OverflowError as exc:
        raise BookingDateError("Booking date has no representable next day") from exc
    try:
        starts_at = _unique_boundary(selected_date, time.min, zone)
        ends_at = _unique_boundary(next_date, time.min, zone)
    except OverflowError as exc:
        raise BookingDateError("Booking date cannot map to UTC") from exc
    if starts_at >= ends_at:
        raise AvailabilityConfigurationError("Business-day boundaries are invalid")
    return TimeInterval(starts_at=starts_at, ends_at=ends_at)


def _first_utc_for_local_date(local_date: date, zone: ZoneInfo) -> datetime:
    """Find the first instant of a local date, including a midnight gap or fold."""
    midnight = datetime.combine(local_date, time.min).replace(tzinfo=zone)
    candidates = sorted({midnight.replace(fold=fold).astimezone(UTC) for fold in (0, 1)})
    low, high = candidates[0], candidates[-1]
    if low.astimezone(zone).date() >= local_date:
        return low
    if high.astimezone(zone).date() < local_date:
        raise AvailabilityConfigurationError("Local calendar boundary is invalid")
    while high - low > timedelta(microseconds=1):
        middle = low + (high - low) // 2
        if middle.astimezone(zone).date() >= local_date:
            high = middle
        else:
            low = middle
    return high


def history_day_bounds(*, selected_date: date, zone: ZoneInfo) -> TimeInterval:
    """Map one local calendar date into UTC for historical appointment reads."""
    if not isinstance(selected_date, date) or isinstance(selected_date, datetime):
        raise BookingDateError("Booking date is invalid")
    if not isinstance(zone, ZoneInfo):
        raise AvailabilityConfigurationError("Availability timezone is invalid")
    try:
        next_date = selected_date + timedelta(days=1)
        starts_at = _first_utc_for_local_date(selected_date, zone)
        ends_at = _first_utc_for_local_date(next_date, zone)
    except OverflowError as exc:
        raise BookingDateError("Booking date cannot map to UTC") from exc
    if starts_at > ends_at:
        raise AvailabilityConfigurationError("Business-day boundaries are invalid")
    return TimeInterval(starts_at=starts_at, ends_at=ends_at)


def _validate_windows(windows: Sequence[ServiceWindow]) -> tuple[ServiceWindow, ...]:
    unique: set[ServiceWindow] = set()
    for window in windows:
        if (
            not isinstance(window, ServiceWindow)
            or not isinstance(window.opens_at, time)
            or not isinstance(window.closes_at, time)
            or window.opens_at.tzinfo is not None
            or window.closes_at.tzinfo is not None
            or window.opens_at >= window.closes_at
        ):
            raise AvailabilityConfigurationError("Service window is invalid")
        unique.add(window)
    ordered = tuple(sorted(unique, key=lambda item: (item.opens_at, item.closes_at)))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.opens_at < previous.closes_at:
            raise AvailabilityConfigurationError("Service windows must not overlap")
    return ordered


def _utc_interval(interval: TimeInterval) -> TimeInterval:
    if not isinstance(interval, TimeInterval):
        raise AvailabilityConfigurationError("Busy interval is invalid")
    starts_at = interval.starts_at
    ends_at = interval.ends_at
    if (
        not isinstance(starts_at, datetime)
        or not isinstance(ends_at, datetime)
        or starts_at.tzinfo is None
        or ends_at.tzinfo is None
        or starts_at.utcoffset() is None
        or ends_at.utcoffset() is None
    ):
        raise AvailabilityConfigurationError("Busy interval must be positive and timezone-aware")
    utc_start = starts_at.astimezone(UTC)
    utc_end = ends_at.astimezone(UTC)
    if utc_start >= utc_end:
        raise AvailabilityConfigurationError("Busy interval must be positive and timezone-aware")
    return TimeInterval(starts_at=utc_start, ends_at=utc_end)


def _valid_start_instants(
    local_date: date, wall_time: time, zone: ZoneInfo
) -> tuple[datetime, ...]:
    return _local_boundary_instants(local_date, wall_time, zone)


def generate_slots(
    *,
    selected_date: date,
    now: datetime,
    policy: AvailabilityPolicy,
    duration_minutes: int,
    windows: Sequence[ServiceWindow],
    busy: Sequence[TimeInterval],
) -> tuple[TimeInterval, ...]:
    """Return sorted, unique UTC slots that fit a single window and avoid busy time."""
    zone = validate_policy(policy)
    validate_booking_date(selected_date=selected_date, now=now, policy=policy)
    if (
        not isinstance(duration_minutes, int)
        or isinstance(duration_minutes, bool)
        or duration_minutes <= 0
    ):
        raise AvailabilityConfigurationError("Service duration must be positive")
    if not isinstance(windows, Sequence) or isinstance(windows, (str, bytes)):
        raise AvailabilityConfigurationError("Service windows are invalid")
    if not isinstance(busy, Sequence) or isinstance(busy, (str, bytes)):
        raise AvailabilityConfigurationError("Busy intervals are invalid")

    ordered_windows = _validate_windows(windows)
    utc_busy = tuple(
        sorted(
            {_utc_interval(interval) for interval in busy},
            key=lambda interval: (interval.starts_at, interval.ends_at),
        )
    )
    day_bounds = business_day_bounds(selected_date=selected_date, zone=zone)
    try:
        duration = timedelta(minutes=duration_minutes)
        step = timedelta(minutes=policy.start_step_minutes)
    except OverflowError as exc:
        raise AvailabilityConfigurationError(
            "Availability duration or step is out of range"
        ) from exc
    slots: set[tuple[datetime, datetime]] = set()

    for window in ordered_windows:
        window_start = _unique_boundary(selected_date, window.opens_at, zone)
        window_end = _unique_boundary(selected_date, window.closes_at, zone)
        if window_start < day_bounds.starts_at or window_end > day_bounds.ends_at:
            raise AvailabilityConfigurationError("Service window lies outside its business day")
        wall_start = datetime.combine(selected_date, window.opens_at)
        wall_end = datetime.combine(selected_date, window.closes_at)
        candidate = wall_start
        while candidate < wall_end:
            for start_at in _valid_start_instants(selected_date, candidate.time(), zone):
                try:
                    end_at = start_at + duration
                except OverflowError as exc:
                    raise AvailabilityConfigurationError(
                        "Service duration is out of range"
                    ) from exc
                if start_at < window_start or end_at > window_end:
                    continue
                if any(
                    start_at < interval.ends_at and end_at > interval.starts_at
                    for interval in utc_busy
                ):
                    continue
                slots.add((start_at, end_at))
            try:
                candidate += step
            except OverflowError as exc:
                raise AvailabilityConfigurationError("Availability step is out of range") from exc

    return tuple(TimeInterval(starts_at=start, ends_at=end) for start, end in sorted(slots))
