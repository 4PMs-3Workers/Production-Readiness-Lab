"""Bounded transaction ownership around the prerequisite's shared expiry transition."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.transactions import CommitOutcomeUnknown, commit_safely, invalidate, rollback
from app.services.change_proposals import expire_change_proposal_in_transaction
from app.worker.models import CandidateResult, FailureReason, Outcome
from app.worker.resources import SchemaCompatibilityError, validate_schema

SessionFactory = Callable[[], AsyncSession]


async def _finish_session(db: AsyncSession, deadline: float) -> None:
    try:
        async with asyncio.timeout_at(deadline):
            await rollback(db)
            await db.close()
    except (SQLAlchemyError, TimeoutError, asyncio.CancelledError):
        try:
            async with asyncio.timeout_at(deadline):
                await invalidate(db)
        except (SQLAlchemyError, TimeoutError, asyncio.CancelledError):
            pass


async def process_candidate(
    sessions: SessionFactory,
    proposal_id: UUID,
    settings: Settings,
    request_id: str,
) -> CandidateResult:
    started = asyncio.get_running_loop().time()
    deadline = started + settings.request_timeout_seconds
    # Reserve part of the total budget for rollback/connection cleanup.
    work_deadline = deadline - min(
        settings.db_query_timeout_seconds, settings.request_timeout_seconds / 4
    )
    outcome, reason = Outcome.FAILED, None
    db = sessions()
    try:
        async with asyncio.timeout_at(work_deadline):
            async with asyncio.timeout(settings.db_query_timeout_seconds):
                transaction = await db.begin()
                await validate_schema(db, settings)
                transition = await expire_change_proposal_in_transaction(
                    db, proposal_id=proposal_id, settings=settings, request_id=request_id
                )
                if transition == "EXPIRED":
                    await db.flush()
                    await commit_safely(db, transaction)
                    outcome = Outcome.EXPIRED
                elif transition == "INCONSISTENT":
                    reason = FailureReason.INCONSISTENT
                elif transition in {"TERMINAL", "NOT_FOUND", "NOT_DUE"}:
                    outcome = Outcome.NOOP
                else:
                    reason = FailureReason.UNEXPECTED_FAILURE
    except CommitOutcomeUnknown:
        reason = FailureReason.COMMIT_UNKNOWN
    except SchemaCompatibilityError:
        reason = FailureReason.SCHEMA_INCOMPATIBLE
    except TimeoutError:
        reason = FailureReason.DEADLINE_EXCEEDED
    except (SQLAlchemyError, AppError):
        reason = FailureReason.DATABASE_UNAVAILABLE
    except asyncio.CancelledError:
        raise
    except Exception:
        reason = FailureReason.UNEXPECTED_FAILURE
    finally:
        await _finish_session(db, deadline)
    return CandidateResult(
        proposal_id, request_id, outcome, 1, asyncio.get_running_loop().time() - started, reason
    )
