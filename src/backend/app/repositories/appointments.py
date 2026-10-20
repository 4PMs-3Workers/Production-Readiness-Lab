"""Caller-transaction appointment reads and atomic booking writes."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    ServiceSetting,
    User,
)
from app.schemas.appointments import AppointmentListQuery

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


async def find_student_appointment(
    db: AsyncSession, *, appointment_id: UUID, student_id: UUID
) -> Row[Any] | None:
    """Find current detail only when both the record and authenticated owner match."""
    statement = (
        _student_detail_statement()
        .where(Appointment.id == appointment_id, Appointment.student_id == student_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(statement)).one_or_none()


def _student_detail_statement():
    # Historical eligibility is deliberately independent of current catalog flags.
    return (
        select(
            *Appointment.__table__.c,
            Purpose.code.label("purpose_code"),
            Purpose.name.label("purpose_name"),
            User.name.label("staff_name"),
        )
        .join(Purpose, Purpose.id == Appointment.purpose_id)
        .join(User, User.id == Appointment.staff_id)
    )


async def read_student_appointment_page(
    db: AsyncSession, query: AppointmentListQuery, *, student_id: UUID
) -> tuple[Sequence[Row[Any]], int]:
    filters = [Appointment.student_id == student_id]
    if query.status is not None:
        filters.append(Appointment.status == query.status)
    total = int(await db.scalar(select(func.count()).select_from(Appointment).where(*filters)) or 0)
    offset = (query.page - 1) * query.page_size
    if offset >= total:
        return (), total
    statement = (
        _student_detail_statement()
        .where(*filters)
        .order_by(Appointment.starts_at.desc(), Appointment.id.desc())
        .limit(query.page_size)
        .offset(offset)
    )
    return (await db.execute(statement)).all(), total


def _staff_detail_statement():
    """Select the historical appointment snapshot and its current display names."""
    student = User.__table__.alias("appointment_student")
    return (
        select(
            *Appointment.__table__.c,
            Purpose.code.label("purpose_code"),
            Purpose.name.label("purpose_name"),
            student.c.name.label("student_name"),
        )
        .join(Purpose, Purpose.id == Appointment.purpose_id)
        .join(student, student.c.id == Appointment.student_id)
    )


async def read_staff_appointment_page(
    db: AsyncSession,
    query: Any,
    *,
    staff_id: UUID,
    date_bounds: tuple[Any, Any] | None = None,
) -> tuple[Sequence[Row[Any]], int]:
    """Read one staff member's assigned history without catalog eligibility filters."""
    filters = [Appointment.staff_id == staff_id]
    if query.status is not None:
        filters.append(Appointment.status == query.status)
    if date_bounds is not None:
        starts_at, ends_at = date_bounds
        filters.extend((Appointment.starts_at >= starts_at, Appointment.starts_at < ends_at))
    total = int(await db.scalar(select(func.count()).select_from(Appointment).where(*filters)) or 0)
    offset = (query.page - 1) * query.page_size
    if offset >= total:
        return (), total
    statement = (
        _staff_detail_statement()
        .where(*filters)
        .order_by(Appointment.starts_at.asc(), Appointment.id.asc())
        .limit(query.page_size)
        .offset(offset)
    )
    return (await db.execute(statement)).all(), total


async def find_staff_appointment(
    db: AsyncSession, *, appointment_id: UUID, staff_id: UUID
) -> Row[Any] | None:
    """Find detail only when the authenticated staff member owns the assignment."""
    statement = (
        _staff_detail_statement()
        .where(Appointment.id == appointment_id, Appointment.staff_id == staff_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(statement)).one_or_none()


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


async def find_cancellation_accounts(
    db: AsyncSession, *, appointment_id: UUID, student_id: UUID
) -> Row[Any] | None:
    """Discover lock identities only; all mutable facts are re-read under locks."""
    statement = select(Appointment.student_id, Appointment.staff_id).where(
        Appointment.id == appointment_id, Appointment.student_id == student_id
    )
    return (await db.execute(statement)).one_or_none()


async def find_staff_appointment_accounts(
    db: AsyncSession, *, appointment_id: UUID, staff_id: UUID
) -> Row[Any] | None:
    """Discover participant lock identities for an assigned staff appointment."""
    statement = select(Appointment.student_id, Appointment.staff_id).where(
        Appointment.id == appointment_id, Appointment.staff_id == staff_id
    )
    return (await db.execute(statement)).one_or_none()


async def lock_appointment(db: AsyncSession, appointment_id: UUID) -> Appointment | None:
    return await db.scalar(
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def lock_pending_proposal(db: AsyncSession, appointment_id: UUID) -> ChangeProposal | None:
    return await db.scalar(
        select(ChangeProposal)
        .where(ChangeProposal.appointment_id == appointment_id, ChangeProposal.status == "PENDING")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
