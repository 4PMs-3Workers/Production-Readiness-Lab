"""Proposal queries using the caller's transaction and appointment lock."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Appointment, ChangeProposal, StaffPurpose
from app.schemas.change_proposals import ProposalHistoryQuery


async def find_receipt(db: AsyncSession, appointment_id: UUID, key: str) -> ChangeProposal | None:
    return await db.scalar(
        select(ChangeProposal)
        .where(
            ChangeProposal.appointment_id == appointment_id,
            ChangeProposal.creation_request_key == key,
        )
        .execution_options(populate_existing=True)
    )


async def find_scoped(
    db: AsyncSession, appointment_id: UUID, proposal_id: UUID
) -> ChangeProposal | None:
    return await db.scalar(
        select(ChangeProposal)
        .where(ChangeProposal.appointment_id == appointment_id, ChangeProposal.id == proposal_id)
        .execution_options(populate_existing=True)
    )


async def lock_proposals(
    db: AsyncSession, appointment_id: UUID, target_id: UUID | None = None
) -> Sequence[ChangeProposal]:
    return (
        await db.scalars(
            select(ChangeProposal)
            .where(
                ChangeProposal.appointment_id == appointment_id,
                or_(ChangeProposal.status == "PENDING", ChangeProposal.id == target_id),
            )
            .order_by(ChangeProposal.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()


async def lock_acceptance(db: AsyncSession, appointment: Appointment) -> StaffPurpose | None:
    return await db.scalar(
        select(StaffPurpose)
        .where(
            StaffPurpose.staff_id == appointment.staff_id,
            StaffPurpose.purpose_id == appointment.purpose_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def history_snapshot(
    db: AsyncSession,
    appointment_id: UUID,
    actor_id: UUID,
    actor_role: str,
    query: ProposalHistoryQuery,
) -> Sequence[Row[Any]]:
    # One consistent statement snapshot, including context when the page is empty.
    page = (
        select(ChangeProposal)
        .where(ChangeProposal.appointment_id == appointment_id)
        .order_by(ChangeProposal.created_at.desc(), ChangeProposal.id.desc())
        .limit(query.page_size)
        .offset(min((query.page - 1) * query.page_size, 2**63 - 1))
        .subquery()
    )
    item = aliased(ChangeProposal, page)
    pending = (
        select(ChangeProposal.id)
        .where(ChangeProposal.appointment_id == appointment_id, ChangeProposal.status == "PENDING")
        .scalar_subquery()
    )
    total = (
        select(func.count())
        .select_from(ChangeProposal)
        .where(ChangeProposal.appointment_id == appointment_id)
        .scalar_subquery()
    )
    owner = Appointment.staff_id if actor_role == "STAFF" else Appointment.student_id
    statement = (
        select(
            Appointment.id, Appointment.status, Appointment.proposal_version, pending, total, item
        )
        .select_from(Appointment)
        .outerjoin(item, true())
        .where(Appointment.id == appointment_id, owner == actor_id)
        .order_by(item.created_at.desc(), item.id.desc())
        .execution_options(populate_existing=True)
    )
    return (await db.execute(statement)).all()
