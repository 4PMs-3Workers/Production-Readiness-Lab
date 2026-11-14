"""Read and replace the school-wide booking policy as a single resource."""

from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.domain.auth import AuthContext
from app.models import AuditEvent, ServiceSetting
from app.repositories import booking_configuration, users
from app.repositories.booking_configuration import WindowKey
from app.schemas.admin_booking_configuration import (
    BookingConfiguration,
    BookingConfigurationReplace,
    WeeklyWindow,
)
from app.services.admin_access import lock_admin, read_under_policy_lock
from app.services.admin_writes import write_under_policy_lock

POLICY_FIELDS = (
    "timezone",
    "start_step_minutes",
    "booking_horizon_days",
    "max_active_appointments",
)


def _snapshot(policy: ServiceSetting, windows: Iterable[WindowKey]) -> BookingConfiguration:
    return BookingConfiguration(
        timezone=policy.timezone,
        start_step_minutes=policy.start_step_minutes,
        booking_horizon_days=policy.booking_horizon_days,
        max_active_appointments=policy.max_active_appointments,
        windows=[
            WeeklyWindow(weekday=weekday, opens_at=opens_at, closes_at=closes_at)
            for weekday, opens_at, closes_at in sorted(windows)
        ],
        updated_at=policy.updated_at,
    )


async def get_configuration(
    db: AsyncSession, *, context: AuthContext, settings: Settings
) -> BookingConfiguration:
    async with read_under_policy_lock(db, context, settings) as policy:
        return _snapshot(policy, await booking_configuration.read_week(db))


async def replace_configuration(
    db: AsyncSession,
    request: BookingConfigurationReplace,
    *,
    context: AuthContext,
    settings: Settings,
    request_id: str,
) -> BookingConfiguration:
    desired = {(item.weekday, item.opens_at, item.closes_at) for item in request.windows}

    async def operation(policy: ServiceSetting) -> tuple[BookingConfiguration, bool]:
        await lock_admin(db, context, settings)
        current = await booking_configuration.read_week(db)
        changes = {
            key: getattr(request, key)
            for key in POLICY_FIELDS
            if getattr(policy, key) != getattr(request, key)
        }
        windows_changed = current.keys() != desired
        if not changes and not windows_changed:
            return _snapshot(policy, current), False
        if policy.updated_at != request.expected_updated_at:
            raise AppError(
                409,
                "BOOKING_CONFIGURATION_VERSION_CONFLICT",
                "Booking configuration changed; reload it",
                "input",
            )

        for key, value in changes.items():
            setattr(policy, key, value)
        added, removed = await booking_configuration.replace_week(db, current, desired)
        policy.updated_at = max(
            await users.utc_now(db), policy.updated_at + timedelta(microseconds=1)
        )
        db.add(
            AuditEvent(
                actor_id=context.user_id,
                action="BOOKING_CONFIGURATION_UPDATED",
                entity_type="SERVICE_SETTINGS",
                entity_id="1",
                request_id=request_id,
                event_metadata={
                    "changed_fields": sorted([*changes, *(["windows"] if windows_changed else [])]),
                    "windows_added": added,
                    "windows_removed": removed,
                },
            )
        )
        return _snapshot(policy, desired), True

    return await write_under_policy_lock(db, operation, settings=settings)
