"""Caller-transaction appointment reads and atomic booking writes."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AuditEvent, Notification, ServiceSetting

BLOCKING_STATUSES = ("UPCOMING", "CHANGE_PENDING")


async def find_creation_request(
    db: AsyncSession, *, student_id: UUID, key: str
) -> Appointment | None:
    """Find a student's committed idempotency receipt without exposing its hash."""
    statement = (
        select(Appointment)
        .where(
            Appointment.student_id == student_id,
            Appointment.creation_request_key == key,
        )
        .execution_options(populate_existing=True)
    )
    return await db.scalar(statement)


async def lock_service_settings(db: AsyncSession) -> ServiceSetting | None:
    """Take the shared policy lock before any account lock in a booking transaction."""
    statement = (
        select(ServiceSetting)
        .where(ServiceSetting.id == 1)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return await db.scalar(statement)


async def count_blocking_for_student(db: AsyncSession, *, student_id: UUID) -> int:
    """Count quota-consuming appointments across all dates."""
    statement = (
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.student_id == student_id,
            Appointment.status.in_(BLOCKING_STATUSES),
        )
    )
    return int(await db.scalar(statement) or 0)


async def add_creation_bundle(
    db: AsyncSession,
    *,
    appointment: Appointment,
    audit_event: AuditEvent,
    notifications: tuple[Notification, Notification],
) -> None:
    """Stage the appointment and its two required side effects in one transaction."""
    db.add_all([appointment, audit_event, *notifications])
