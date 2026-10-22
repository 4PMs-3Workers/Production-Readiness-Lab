from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Appointment, AuditEvent, ChangeProposal, Notification


@dataclass(frozen=True)
class WorkerEnv:
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    settings: Settings
    student_id: UUID
    staff_id: UUID
    purpose_id: UUID
    now: datetime
    start: datetime


async def add_proposal(
    env: WorkerEnv,
    *,
    identifier: UUID | None = None,
    expiry: datetime | None = None,
    proposal_status: str = "PENDING",
    appointment_status: str = "CHANGE_PENDING",
) -> tuple[UUID, UUID]:
    expiry = expiry if expiry is not None else env.now - timedelta(seconds=5)
    async with env.factory.begin() as db:
        row = Appointment(
            student_id=env.student_id,
            staff_id=env.staff_id,
            purpose_id=env.purpose_id,
            duration_minutes=30,
            starts_at=env.start,
            ends_at=env.start + timedelta(minutes=30),
            status=appointment_status,
            proposal_version=1,
            creation_request_key=uuid4().hex,
            creation_request_hash=bytes(32),
            cancelled_at=env.now if appointment_status == "CANCELLED" else None,
            outcome_at=env.now if appointment_status in {"COMPLETED", "NO_SHOW"} else None,
            other_reason="private-worker-fixture-consultation",
        )
        db.add(row)
        await db.flush()
        proposal = ChangeProposal(
            id=identifier or uuid4(),
            appointment_id=row.id,
            created_by=env.staff_id,
            creation_request_key=uuid4().hex,
            creation_request_hash=bytes(32),
            created_version=1,
            created_at=min(expiry, env.now) - timedelta(days=1),
            proposed_starts_at=env.start + timedelta(hours=1),
            proposed_ends_at=env.start + timedelta(hours=1, minutes=30),
            proposed_location="Private fixture room",
            expires_at=expiry,
            status=proposal_status,
            resolved_at=env.now if proposal_status != "PENDING" else None,
            resolved_by=(env.student_id if proposal_status in {"ACCEPTED", "DECLINED"} else None),
        )
        db.add(proposal)
        await db.flush()
        return proposal.id, row.id


async def side_effect_counts(env: WorkerEnv) -> tuple[int, int]:
    async with env.factory() as db:
        return (
            int(await db.scalar(select(func.count()).select_from(AuditEvent)) or 0),
            int(await db.scalar(select(func.count()).select_from(Notification)) or 0),
        )
