"""Bounded, read-only database reads for daily appointment availability."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.availability import ServiceWindow, TimeInterval
from app.models import Appointment, ServiceHour, ServiceSetting, StaffUnavailability

BLOCKING_STATUSES = ("UPCOMING", "CHANGE_PENDING")


async def read_settings(db: AsyncSession) -> ServiceSetting | None:
    """Read the required singleton policy row without creating a fallback."""
    return await db.get(ServiceSetting, 1)


async def read_windows(db: AsyncSession, weekday: int) -> tuple[ServiceWindow, ...]:
    """Read only the local wall-time windows configured for an ISO weekday."""
    statement = (
        select(ServiceHour.opens_at, ServiceHour.closes_at)
        .where(ServiceHour.weekday == weekday)
        .order_by(ServiceHour.opens_at.asc(), ServiceHour.closes_at.asc())
    )
    return tuple(
        ServiceWindow(opens_at, closes_at) for opens_at, closes_at in await db.execute(statement)
    )


async def read_unavailability(
    db: AsyncSession, *, staff_id: UUID, bounds: TimeInterval
) -> tuple[TimeInterval, ...]:
    """Read selected-staff busy intervals overlapping the half-open business day."""
    statement = (
        select(StaffUnavailability.starts_at, StaffUnavailability.ends_at)
        .where(
            StaffUnavailability.staff_id == staff_id,
            StaffUnavailability.starts_at < bounds.ends_at,
            StaffUnavailability.ends_at > bounds.starts_at,
        )
        .order_by(StaffUnavailability.starts_at.asc(), StaffUnavailability.ends_at.asc())
    )
    return tuple(
        TimeInterval(starts_at, ends_at) for starts_at, ends_at in await db.execute(statement)
    )


async def read_staff_appointments(
    db: AsyncSession, *, staff_id: UUID, bounds: TimeInterval
) -> tuple[TimeInterval, ...]:
    """Read selected-staff conflicts across all purposes, limited to blocking states."""
    statement = (
        select(Appointment.starts_at, Appointment.ends_at)
        .where(
            Appointment.staff_id == staff_id,
            Appointment.status.in_(BLOCKING_STATUSES),
            Appointment.starts_at < bounds.ends_at,
            Appointment.ends_at > bounds.starts_at,
        )
        .order_by(Appointment.starts_at.asc(), Appointment.ends_at.asc())
    )
    return tuple(
        TimeInterval(starts_at, ends_at) for starts_at, ends_at in await db.execute(statement)
    )


async def read_student_appointments(
    db: AsyncSession, *, student_id: UUID, bounds: TimeInterval
) -> tuple[TimeInterval, ...]:
    """Read the student's conflicts across all staff and purposes."""
    statement = (
        select(Appointment.starts_at, Appointment.ends_at)
        .where(
            Appointment.student_id == student_id,
            Appointment.status.in_(BLOCKING_STATUSES),
            Appointment.starts_at < bounds.ends_at,
            Appointment.ends_at > bounds.starts_at,
        )
        .order_by(Appointment.starts_at.asc(), Appointment.ends_at.asc())
    )
    return tuple(
        TimeInterval(starts_at, ends_at) for starts_at, ends_at in await db.execute(statement)
    )
