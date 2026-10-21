"""Sequential bounded batches with fresh transactions and durable-state fairness."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories import expiry_worker
from app.worker.models import (
    Candidate,
    CandidateResult,
    FailureReason,
    Outcome,
    WorkerIdentity,
    WorkerState,
)
from app.worker.processing import SessionFactory, _finish_session, process_candidate
from app.worker.reporting import Reporter
from app.worker.resources import CleanupDeadline, SchemaCompatibilityError, earlier_deadline

Processor = Callable[[SessionFactory, UUID, Settings, str], Awaitable[CandidateResult]]


class ScanFailure(Exception):
    def __init__(self, reason: FailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class BatchRunner:
    def __init__(
        self,
        sessions: SessionFactory,
        settings: Settings,
        *,
        reporter: Reporter | None = None,
        identity: WorkerIdentity | None = None,
        processor: Processor | None = None,
        cleanup_deadline: CleanupDeadline | None = None,
    ) -> None:
        self.sessions, self.settings = sessions, settings
        self.identity = identity or WorkerIdentity()
        self.state = WorkerState(self.identity.worker_id)
        self.reporter = reporter or Reporter()
        self.processor = processor or process_candidate
        self.cleanup_deadline = cleanup_deadline
        self.cursor: Candidate | None = None
        self.sweep_until: datetime | None = None

    async def run_batch(self, stop: asyncio.Event | None = None) -> bool:
        """Return False if already stopped; failures never masquerade as an empty scan."""
        if stop is not None and stop.is_set():
            return False
        started = asyncio.get_running_loop().time()
        self.state.current_run_id = run_id = uuid4()
        self.state.batch_expired = self.state.batch_noops = self.state.batch_failed = 0
        self.state.selected = 0
        db: AsyncSession | None = None
        deadline = started + self.settings.request_timeout_seconds
        try:
            async with asyncio.timeout_at(
                deadline
                - min(
                    self.settings.db_query_timeout_seconds,
                    self.settings.request_timeout_seconds / 4,
                )
            ):
                db = self.sessions()
                snapshot = await expiry_worker.discover(
                    db, self.settings, after=self.cursor, due_before=self.sweep_until
                )
        except SchemaCompatibilityError:
            self._scan_failed(started, FailureReason.SCHEMA_INCOMPATIBLE)
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            self._scan_failed(started, FailureReason.DATABASE_UNAVAILABLE)
            raise ScanFailure(FailureReason.DATABASE_UNAVAILABLE) from None
        finally:
            if db is not None:
                await _finish_session(db, earlier_deadline(deadline, self.cleanup_deadline))
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        state = self.state
        state.database_ok = True
        state.successful_scans += 1
        state.last_successful_scan = snapshot.database_now
        state.selected = len(snapshot.candidates)
        state.backlog_count, state.backlog_capped = snapshot.backlog_count, snapshot.backlog_capped
        state.oldest_overdue_seconds = snapshot.oldest_overdue_seconds
        state.phase = "running"
        if state.successful_scans == 1:
            self.reporter.emit("ready", state)
        if not snapshot.candidates:
            self.cursor = self.sweep_until = None
        elif self.sweep_until is None:
            # Finish this finite due cohort before wrapping to retry older failed rows.
            self.sweep_until = snapshot.database_now
        for candidate in snapshot.candidates:
            if stop is not None and stop.is_set():
                break
            request_id = self.identity.request_id(run_id, candidate.proposal_id)
            candidate_started = asyncio.get_running_loop().time()
            try:
                result = await self.processor(
                    self.sessions, candidate.proposal_id, self.settings, request_id
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = CandidateResult(
                    candidate.proposal_id,
                    request_id,
                    Outcome.FAILED,
                    0,
                    asyncio.get_running_loop().time() - candidate_started,
                    FailureReason.UNEXPECTED_FAILURE,
                )
            self.cursor = candidate
            if result.outcome == Outcome.EXPIRED:
                state.expired += 1
                state.batch_expired += 1
            elif result.outcome == Outcome.NOOP:
                state.noops += 1
                state.batch_noops += 1
            else:
                state.failed += 1
                state.batch_failed += 1
                if result.reason != FailureReason.INCONSISTENT:
                    state.database_ok = False
            self.reporter.emit("candidate", state, result=result)
            if result.reason == FailureReason.SCHEMA_INCOMPATIBLE:
                raise SchemaCompatibilityError()
        state.last_scan_duration_seconds = asyncio.get_running_loop().time() - started
        self.reporter.emit("scan", state)
        return True

    def _scan_failed(self, started: float, reason: FailureReason) -> None:
        state = self.state
        state.failed_scans += 1
        state.database_ok = False
        state.last_scan_duration_seconds = asyncio.get_running_loop().time() - started
        self.reporter.emit("scan", state, reason=reason)
