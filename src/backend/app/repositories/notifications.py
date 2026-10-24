"""Recipient-scoped notification reads and conditional state updates."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Notification


def _notification_statement():
    return select(
        Notification,
        Appointment.student_id.label("appointment_student_id"),
        Appointment.staff_id.label("appointment_staff_id"),
        Appointment.starts_at.label("appointment_starts_at"),
        Appointment.status.label("appointment_status"),
    ).outerjoin(Appointment, Appointment.id == Notification.appointment_id)


async def read_notification_page(
    db: AsyncSession,
    *,
    recipient_id: UUID,
    page: int,
    page_size: int,
    unread_only: bool,
) -> tuple[Sequence[Row[Any]], int]:
    """Read only the recipient's notifications with stable history ordering."""
    filters = [Notification.recipient_id == recipient_id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))

    total = int(
        await db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    )
    offset = (page - 1) * page_size
    if offset >= total:
        return (), total

    statement = (
        _notification_statement()
        .where(*filters)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(page_size)
        .offset(offset)
    )
    return (await db.execute(statement)).all(), total


async def mark_read_if_unread(
    db: AsyncSession,
    *,
    notification_id: UUID,
    recipient_id: UUID,
    read_at: datetime,
) -> tuple[bool, bool, datetime | None]:
    """Return existence, whether this call updated the row, and its read time."""
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_id == recipient_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=read_at)
            .execution_options(synchronize_session=False)
        ),
    )
    row = (
        await db.execute(
            select(Notification.id, Notification.read_at).where(
                Notification.id == notification_id,
                Notification.recipient_id == recipient_id,
            )
        )
    ).one_or_none()
    if row is None:
        return False, False, None
    return True, result.rowcount == 1, row.read_at


async def read_notification_state(
    db: AsyncSession, *, notification_id: UUID, recipient_id: UUID
) -> tuple[bool, datetime | None]:
    row = (
        await db.execute(
            select(Notification.id, Notification.read_at).where(
                Notification.id == notification_id,
                Notification.recipient_id == recipient_id,
            )
        )
    ).one_or_none()
    if row is None:
        return False, None
    return True, row.read_at
