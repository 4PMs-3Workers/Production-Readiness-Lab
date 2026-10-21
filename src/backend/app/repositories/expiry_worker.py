"""Nonlocking, bounded discovery; a cursor is a fairness hint, never a work queue."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.transactions import read_transaction
from app.models import ChangeProposal
from app.repositories import users
from app.worker.models import Candidate, ScanSnapshot
from app.worker.resources import validate_schema


async def discover(
    db: AsyncSession, settings: Settings, *, after: Candidate | None = None
) -> ScanSnapshot:
    async with read_transaction(db, settings):
        await validate_schema(db, settings)
        now = await users.utc_now(db)
        due = (ChangeProposal.status == "PENDING", ChangeProposal.expires_at <= now)
        # Cap the monitoring query too: count is a lower bound when capped, not COUNT(*).
        backlog = (
            select(ChangeProposal.expires_at)
            .where(*due)
            .order_by(ChangeProposal.expires_at, ChangeProposal.id)
            .limit(settings.worker_batch_size + 1)
            .subquery()
        )
        count, oldest = (
            await db.execute(select(func.count(), func.min(backlog.c.expires_at)))
        ).one()
        statement = select(ChangeProposal.id, ChangeProposal.expires_at).where(*due)
        if after is not None:
            statement = statement.where(
                or_(
                    ChangeProposal.expires_at > after.expires_at,
                    and_(
                        ChangeProposal.expires_at == after.expires_at,
                        ChangeProposal.id > after.proposal_id,
                    ),
                )
            )
        rows = (
            await db.execute(
                statement.order_by(ChangeProposal.expires_at, ChangeProposal.id).limit(
                    settings.worker_batch_size
                )
            )
        ).all()
        return ScanSnapshot(
            database_now=now,
            candidates=tuple(Candidate(identifier, expiry) for identifier, expiry in rows),
            backlog_count=min(count, settings.worker_batch_size),
            backlog_capped=count > settings.worker_batch_size,
            oldest_overdue_seconds=(now - oldest).total_seconds() if oldest is not None else None,
        )
