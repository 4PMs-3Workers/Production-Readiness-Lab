"""Small typed worker observations; business transitions stay in shared services."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class Outcome(StrEnum):
    EXPIRED = "EXPIRED"
    NOOP = "NOOP"
    FAILED = "FAILED"


class FailureReason(StrEnum):
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    COMMIT_UNKNOWN = "COMMIT_UNKNOWN"
    INCONSISTENT = "INCONSISTENT"
    UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"
    SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"


@dataclass(frozen=True)
class Candidate:
    proposal_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class ScanSnapshot:
    database_now: datetime
    candidates: tuple[Candidate, ...]
    backlog_count: int
    backlog_capped: bool
    oldest_overdue_seconds: float | None


@dataclass(frozen=True)
class CandidateResult:
    proposal_id: UUID
    request_id: str
    outcome: Outcome
    attempts: int
    duration_seconds: float
    reason: FailureReason | None = None


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: UUID = field(default_factory=uuid4)

    def request_id(self, run_id: UUID, proposal_id: UUID) -> str:
        return f"expiry.{self.worker_id.hex}.{run_id.hex}.{proposal_id.hex}"


@dataclass
class WorkerState:
    worker_id: UUID
    current_run_id: UUID | None = None
    database_ok: bool = False
    last_successful_scan: datetime | None = None
    last_scan_duration_seconds: float | None = None
    successful_scans: int = 0
    failed_scans: int = 0
    expired: int = 0
    noops: int = 0
    failed: int = 0
    reporting_failures: int = 0
    backlog_count: int | None = None
    backlog_capped: bool = False
    oldest_overdue_seconds: float | None = None
