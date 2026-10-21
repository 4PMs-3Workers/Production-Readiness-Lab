"""Bounded transaction ownership around the prerequisite's shared expiry transition."""

import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    invalidate,
    mysql_error_code,
    rollback,
)
from app.services.change_proposals import expire_change_proposal_in_transaction
from app.worker.models import CandidateResult, FailureReason, Outcome
from app.worker.resources import SchemaCompatibilityError, validate_schema

SessionFactory = Callable[[], AsyncSession]


async def _finish_session(db: AsyncSession, deadline: float) -> None:
    try:
        async with asyncio.timeout_at(deadline):
            await rollback(db)
            await db.close()
    except (Exception, asyncio.CancelledError):
        try:
            async with asyncio.timeout_at(deadline):
                await invalidate(db)
        except (Exception, asyncio.CancelledError):
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
    attempts = 0
    for attempt in range(settings.max_deadlock_retries + 1):
        attempts += 1
        retry = False
        db: AsyncSession | None = None
        try:
            async with asyncio.timeout_at(work_deadline):
                db = sessions()
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
        except SQLAlchemyError as error:
            reason = FailureReason.DATABASE_UNAVAILABLE
            retry = (
                mysql_error_code(error) in RETRYABLE_MYSQL_ERRORS
                and attempt < settings.max_deadlock_retries
            )
        except AppError:
            reason = FailureReason.DATABASE_UNAVAILABLE
        except asyncio.CancelledError:
            raise
        except Exception:
            reason = FailureReason.UNEXPECTED_FAILURE
        finally:
            if db is not None:
                await _finish_session(db, deadline)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if not retry:
            break
        remaining = work_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            reason = FailureReason.DEADLINE_EXCEEDED
            break
        await asyncio.sleep(min(0.01 * 2 ** min(attempt, 10), remaining))
        reason = None
    return CandidateResult(
        proposal_id,
        request_id,
        outcome,
        attempts,
        asyncio.get_running_loop().time() - started,
        reason,
    )
