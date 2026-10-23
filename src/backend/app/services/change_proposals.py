"""Locked proposal creation, durable replay, student decisions and shared expiry."""

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, TypeVar, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, ServiceUnavailable
from app.core.config import Settings
from app.core.transactions import (
    RETRYABLE_MYSQL_ERRORS,
    CommitOutcomeUnknown,
    commit_safely,
    mysql_error_code,
    read_transaction,
    rollback,
)
from app.domain.auth import AuthContext
from app.domain.availability import (
    AvailabilityConfigurationError,
    AvailabilityPolicy,
    BookingDateError,
)
from app.domain.change_proposals import proposal_request_hash
from app.models import Appointment, AuditEvent, ChangeProposal, Notification, ServiceSetting, User
from app.observability.records import SpanName
from app.observability.tracing import SpanFactory, safe_span, set_business_outcome
from app.repositories import appointments, availability, catalog, users
from app.repositories import change_proposals as repository
from app.schemas.change_proposals import (
    MAX_PROPOSAL_VERSION,
    ProposalContext,
    ProposalCreateReceipt,
    ProposalCreateRequest,
    ProposalHistoryItem,
    ProposalHistoryQuery,
    ProposalHistoryResponse,
    ProposalResolution,
)
from app.schemas.common import UtcTimestamp
from app.services.availability_rules import business_day_bounds, generate_slots, validate_policy

T = TypeVar("T")
ExpiryTransition = Literal["EXPIRED", "NOT_DUE", "TERMINAL", "NOT_FOUND", "INCONSISTENT"]
Decision = Literal["ACCEPTED", "DECLINED"]
_CREATION_CONSTRAINT = "uq_change_proposals_creation_request"
_CONSTRAINT_KEY = re.compile(r"for key\s+(['\"`])([^'\"`]+)\1\s*$", re.IGNORECASE)
FailureCallback = Callable[[BaseException], object]


class _NamedDuplicate(Exception):
    """Only the exact proposal creation-key constraint rejected the transaction."""


def _creation_duplicate(error: SQLAlchemyError) -> bool:
    if mysql_error_code(error) != 1062:
        return False
    original = getattr(error, "orig", None)
    args = getattr(original, "args", ())
    message = args[1] if len(args) > 1 and isinstance(args[1], str) else str(original or "")
    match = _CONSTRAINT_KEY.search(message)
    return bool(match and match.group(2).rsplit(".", 1)[-1] == _CREATION_CONSTRAINT)


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Requested appointment or proposal was not found", "input")


def _conflict(code: str) -> AppError:
    return AppError(
        409, code, "The requested proposal operation conflicts with current state", "business"
    )


def _require_role(context: AuthContext, role: str) -> None:
    if context.role != role:
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")


@dataclass
class _Locked:
    appointment: Appointment
    policy: ServiceSetting
    accounts: dict[UUID, User | None]


async def _lock(value: Awaitable[T], settings: Settings) -> T:
    async with asyncio.timeout(settings.db_lock_timeout_seconds):
        return await value


async def _lock_context(
    db: AsyncSession, appointment_id: UUID, context: AuthContext | None, settings: Settings
) -> _Locked | None:
    policy = await _lock(appointments.lock_service_settings(db), settings)
    if policy is None:
        raise ServiceUnavailable()
    statement = select(Appointment.student_id, Appointment.staff_id).where(
        Appointment.id == appointment_id
    )
    if context is not None:
        owner = Appointment.staff_id if context.role == "STAFF" else Appointment.student_id
        statement = statement.where(owner == context.user_id)
    identities = (await db.execute(statement)).one_or_none()
    if identities is None:
        return None
    accounts = {}
    for account_id in sorted(
        {identities.student_id, identities.staff_id}, key=lambda value: value.bytes
    ):
        accounts[account_id] = await _lock(users.lock_by_id(db, account_id), settings)
    row = await _lock(appointments.lock_appointment(db, appointment_id), settings)
    if row is None:
        return None
    if (row.student_id, row.staff_id) != (identities.student_id, identities.staff_id):
        raise ServiceUnavailable()
    if context is not None:
        actor = accounts.get(context.user_id)
        if actor is None or not actor.is_active or actor.role != context.role:
            raise _not_found()
        if (context.role == "STAFF" and row.staff_id != context.user_id) or (
            context.role == "STUDENT" and row.student_id != context.user_id
        ):
            raise _not_found()
    return _Locked(row, policy, accounts)


def _pending(rows: Sequence[ChangeProposal]) -> ChangeProposal | None:
    pending = [row for row in rows if row.status == "PENDING"]
    if len(pending) > 1:
        raise ServiceUnavailable()
    return pending[0] if pending else None


def _consistent(row: Appointment, pending: ChangeProposal | None) -> bool:
    return (row.status == "CHANGE_PENDING") == (pending is not None)


def _receipt(proposal: ChangeProposal) -> ProposalCreateReceipt:
    if proposal.created_version is None or proposal.creation_request_hash is None:
        raise ServiceUnavailable()
    return ProposalCreateReceipt(
        id=proposal.id,
        appointment_id=proposal.appointment_id,
        created_version=proposal.created_version,
        created_at=UtcTimestamp(proposal.created_at),
        expires_at=UtcTimestamp(proposal.expires_at),
    )


def _resolution(proposal: ChangeProposal, actor_id: UUID) -> ProposalResolution:
    if proposal.resolved_at is None or proposal.resolved_by != actor_id:
        raise ServiceUnavailable()
    return ProposalResolution(
        id=proposal.id,
        appointment_id=proposal.appointment_id,
        status=cast(Decision, proposal.status),
        resolved_at=UtcTimestamp(proposal.resolved_at),
        resolved_by=actor_id,
    )


def _effects(
    db: AsyncSession,
    row: Appointment,
    proposal: ChangeProposal,
    action: str,
    actor_id: UUID | None,
    request_id: str,
    superseded_id: UUID | None = None,
) -> None:
    metadata = {"appointment_id": str(row.id), "proposal_id": str(proposal.id)}
    if superseded_id is not None:
        metadata["superseded_id"] = str(superseded_id)
    db.add_all(
        [
            AuditEvent(
                actor_id=actor_id,
                action=action,
                entity_type="CHANGE_PROPOSAL",
                entity_id=str(proposal.id),
                request_id=request_id,
                event_metadata=metadata,
            ),
            *(
                Notification(recipient_id=recipient, appointment_id=row.id, event_type=action)
                for recipient in (row.student_id, row.staff_id)
            ),
        ]
    )


async def _validate_interval(
    db: AsyncSession,
    locked: _Locked,
    start: datetime,
    now: datetime,
) -> datetime:
    row, setting = locked.appointment, locked.policy
    for account_id, role in ((row.staff_id, "STAFF"), (row.student_id, "STUDENT")):
        account = locked.accounts.get(account_id)
        if account is None or account.role != role or not account.is_active:
            raise _not_found()
    if await catalog.find_active_purpose(db, row.purpose_id) is None:
        raise _not_found()
    if await catalog.find_accepting_staff(db, row.purpose_id, row.staff_id) is None:
        raise _not_found()
    policy = AvailabilityPolicy(
        timezone=setting.timezone,
        start_step_minutes=setting.start_step_minutes,
        booking_horizon_days=setting.booking_horizon_days,
    )
    zone = validate_policy(policy)
    selected_date = start.astimezone(zone).date()
    bounds = business_day_bounds(selected_date=selected_date, zone=zone)
    windows = await availability.read_windows(db, selected_date.isoweekday())
    unavailable = await availability.read_unavailability(db, staff_id=row.staff_id, bounds=bounds)
    staff_busy = await availability.read_staff_appointments(
        db,
        staff_id=row.staff_id,
        bounds=bounds,
        exclude_appointment_id=row.id,
    )
    student_busy = await availability.read_student_appointments(
        db,
        student_id=row.student_id,
        bounds=bounds,
        exclude_appointment_id=row.id,
    )
    try:
        slots = generate_slots(
            selected_date=selected_date,
            now=now,
            policy=policy,
            duration_minutes=row.duration_minutes,
            windows=windows,
            busy=(*unavailable, *staff_busy),
        )
    except BookingDateError:
        raise _conflict("BOOKING_DATE_INVALID") from None
    end = start + timedelta(minutes=row.duration_minutes)
    if not any(slot.starts_at == start and slot.ends_at == end for slot in slots):
        raise _conflict("STAFF_UNAVAILABLE")
    if any(busy.starts_at < end and busy.ends_at > start for busy in student_busy):
        raise _conflict("STUDENT_UNAVAILABLE")
    return end


async def _write(
    db: AsyncSession,
    operation: Callable[[], Awaitable[tuple[T, bool]]],
    settings: Settings,
    deadline: float,
    *,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> tuple[T, bool]:
    """Each attempt owns its transaction; unknown commits are never replayed here."""
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(settings.max_deadlock_retries + 1):
                try:
                    async with asyncio.timeout(settings.db_query_timeout_seconds):
                        transaction = await db.begin()
                        result, changed = await operation()
                        if changed:
                            await db.flush()
                            await commit_safely(
                                db,
                                transaction,
                                known_abort=_creation_duplicate,
                                span_factory=span_factory,
                                failure_callback=failure_callback,
                            )
                        else:
                            await rollback(
                                db,
                                span_factory=span_factory,
                                failure_callback=failure_callback,
                            )
                        return result, changed
                except SQLAlchemyError as error:
                    if failure_callback is not None:
                        try:
                            failure_callback(error)
                        except BaseException:
                            pass
                    await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
                    if _creation_duplicate(error):
                        raise _NamedDuplicate() from None
                    if (
                        mysql_error_code(error) not in RETRYABLE_MYSQL_ERRORS
                        or attempt >= settings.max_deadlock_retries
                    ):
                        raise ServiceUnavailable() from None
                    remaining = deadline - asyncio.get_running_loop().time()
                    delay = min(0.01 * 2**attempt, max(0.0, remaining))
                    if delay <= 0:
                        raise ServiceUnavailable() from None
                    await asyncio.sleep(delay)
        raise ServiceUnavailable()
    except BookingDateError:
        await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise _conflict("BOOKING_DATE_INVALID") from None
    except (
        TimeoutError,
        CommitOutcomeUnknown,
        AvailabilityConfigurationError,
        OverflowError,
    ) as error:
        await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        if isinstance(error, CommitOutcomeUnknown):
            raise
        raise ServiceUnavailable() from None
    except BaseException:
        await rollback(db, span_factory=span_factory, failure_callback=failure_callback)
        raise


async def _create_proposal_impl(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    payload: ProposalCreateRequest,
    context: AuthContext,
    idempotency_key: str,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> tuple[ProposalCreateReceipt, bool]:
    """Return the immutable creation receipt and whether this is a replay."""
    _require_role(context, "STAFF")
    request_hash = proposal_request_hash(
        payload, appointment_id=appointment_id, actor_id=context.user_id
    )

    async def operation(replay_only: bool = False) -> tuple[ProposalCreateReceipt, bool]:
        locked = await _lock_context(db, appointment_id, context, settings)
        if locked is None:
            raise _not_found()
        row = locked.appointment
        if (
            payload.expected_pending_proposal_id is not None
            and await repository.find_scoped(db, row.id, payload.expected_pending_proposal_id)
            is None
        ):
            raise _not_found()
        saved = await repository.find_receipt(db, row.id, idempotency_key)
        if saved is not None:
            if saved.creation_request_hash != request_hash:
                raise _conflict("IDEMPOTENCY_CONFLICT")
            return _receipt(saved), False
        if replay_only:
            raise ServiceUnavailable()
        proposals = await _lock(repository.lock_proposals(db, row.id), settings)
        pending = _pending(proposals)
        if not _consistent(row, pending):
            raise ServiceUnavailable()
        if (
            row.proposal_version != payload.expected_proposal_version
            or (pending.id if pending is not None else None) != payload.expected_pending_proposal_id
        ):
            raise _conflict("PROPOSAL_PRECONDITION_FAILED")
        if row.status not in appointments.BLOCKING_STATUSES:
            raise _conflict("APPOINTMENT_STATE_CONFLICT")
        await _lock(repository.lock_acceptance(db, row), settings)
        now = await users.utc_now(db)
        if pending is not None and now >= pending.expires_at:
            raise _conflict("PROPOSAL_EXPIRED")
        start = payload.proposed_starts_at
        if start == row.starts_at and payload.proposed_location == row.location:
            raise _conflict("PROPOSAL_NO_CHANGE")
        end = await _validate_interval(db, locked, start, now)
        zone = validate_policy(
            AvailabilityPolicy(
                timezone=locked.policy.timezone,
                start_step_minutes=locked.policy.start_step_minutes,
                booking_horizon_days=locked.policy.booking_horizon_days,
            )
        )
        expires_at = business_day_bounds(
            selected_date=min(row.starts_at.astimezone(zone).date(), start.astimezone(zone).date()),
            zone=zone,
        ).starts_at
        if now >= expires_at:
            raise _conflict("PROPOSAL_EXPIRED")
        if row.proposal_version >= MAX_PROPOSAL_VERSION:
            raise ServiceUnavailable()
        if pending is not None:
            pending.status, pending.resolved_at, pending.resolved_by = (
                "SUPERSEDED",
                now,
                context.user_id,
            )
            await db.flush()  # Release generated pending unique before staging the new INSERT.
        row.proposal_version += 1
        proposal = ChangeProposal(
            id=uuid4(),
            appointment_id=row.id,
            created_by=context.user_id,
            creation_request_key=idempotency_key,
            creation_request_hash=request_hash,
            created_version=row.proposal_version,
            proposed_starts_at=start,
            proposed_ends_at=end,
            proposed_location=payload.proposed_location,
            status="PENDING",
            created_at=now,
            expires_at=expires_at,
        )
        db.add(proposal)
        row.status = "CHANGE_PENDING"
        action = "CHANGE_PROPOSAL_REPLACED" if pending else "CHANGE_PROPOSAL_CREATED"
        _effects(
            db, row, proposal, action, context.user_id, request_id, pending.id if pending else None
        )
        return _receipt(proposal), True

    try:
        receipt, changed = await _write(
            db,
            operation,
            settings,
            deadline,
            span_factory=span_factory,
            failure_callback=failure_callback,
        )
    except _NamedDuplicate:
        receipt, changed = await _write(
            db,
            lambda: operation(replay_only=True),
            settings,
            deadline,
            span_factory=span_factory,
            failure_callback=failure_callback,
        )
    return receipt, not changed


async def create_proposal(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    payload: ProposalCreateRequest,
    context: AuthContext,
    idempotency_key: str,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> tuple[ProposalCreateReceipt, bool]:
    """Create a proposal under an optional owner-injected business span."""
    with safe_span(span_factory, "proposal.create") as span:
        try:
            result = await _create_proposal_impl(
                db,
                appointment_id=appointment_id,
                payload=payload,
                context=context,
                idempotency_key=idempotency_key,
                request_id=request_id,
                settings=settings,
                deadline=deadline,
                span_factory=span_factory,
                failure_callback=failure_callback,
            )
        except CommitOutcomeUnknown:
            set_business_outcome(span, "commit_unknown")
            raise ServiceUnavailable() from None
        except BaseException:
            set_business_outcome(span, "failed")
            raise
        else:
            set_business_outcome(span, "replayed" if result[1] else "committed")
            return result


async def _decide_proposal_impl(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    proposal_id: UUID,
    decision: Decision,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> tuple[ProposalResolution, bool]:
    _require_role(context, "STUDENT")

    async def operation() -> tuple[ProposalResolution, bool]:
        locked = await _lock_context(db, appointment_id, context, settings)
        if locked is None:
            raise _not_found()
        row = locked.appointment
        proposals = await _lock(repository.lock_proposals(db, row.id, proposal_id), settings)
        proposal = next((item for item in proposals if item.id == proposal_id), None)
        if proposal is None:
            raise _not_found()
        if proposal.status == decision:
            return _resolution(proposal, context.user_id), False
        if proposal.status != "PENDING":
            raise _conflict("PROPOSAL_STATE_CONFLICT")
        pending = _pending(proposals)
        if not _consistent(row, pending) or pending is None or pending.id != proposal.id:
            raise ServiceUnavailable()
        if decision == "ACCEPTED":
            await _lock(repository.lock_acceptance(db, row), settings)
        now = await users.utc_now(db)
        if now >= proposal.expires_at:
            raise _conflict("PROPOSAL_EXPIRED")
        if decision == "ACCEPTED":
            end = await _validate_interval(db, locked, proposal.proposed_starts_at, now)
            if end != proposal.proposed_ends_at:
                raise ServiceUnavailable()
            row.starts_at, row.ends_at = proposal.proposed_starts_at, proposal.proposed_ends_at
            row.location, row.status = proposal.proposed_location, "UPCOMING"
        else:
            row.status, row.cancelled_at, row.cancelled_by = "CANCELLED", now, context.user_id
            row.cancel_reason = "CHANGE_PROPOSAL_DECLINED"
        proposal.status, proposal.resolved_at, proposal.resolved_by = decision, now, context.user_id
        _effects(db, row, proposal, f"CHANGE_PROPOSAL_{decision}", context.user_id, request_id)
        return _resolution(proposal, context.user_id), True

    return await _write(
        db,
        operation,
        settings,
        deadline,
        span_factory=span_factory,
        failure_callback=failure_callback,
    )


async def decide_proposal(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    proposal_id: UUID,
    decision: Decision,
    context: AuthContext,
    request_id: str,
    settings: Settings,
    deadline: float,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> ProposalResolution:
    """Resolve a proposal under an optional owner-injected business span."""
    span_name: SpanName = "proposal.accept" if decision == "ACCEPTED" else "proposal.decline"
    with safe_span(span_factory, span_name) as span:
        try:
            result, changed = await _decide_proposal_impl(
                db,
                appointment_id=appointment_id,
                proposal_id=proposal_id,
                decision=decision,
                context=context,
                request_id=request_id,
                settings=settings,
                deadline=deadline,
                span_factory=span_factory,
                failure_callback=failure_callback,
            )
        except CommitOutcomeUnknown:
            set_business_outcome(span, "commit_unknown")
            raise ServiceUnavailable() from None
        except BaseException:
            set_business_outcome(span, "failed")
            raise
        else:
            set_business_outcome(span, "committed" if changed else "noop")
            return result


async def list_proposals(
    db: AsyncSession,
    *,
    appointment_id: UUID,
    query: ProposalHistoryQuery,
    context: AuthContext,
    settings: Settings,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> ProposalHistoryResponse:
    if context.role not in {"STAFF", "STUDENT"}:
        raise AppError(403, "FORBIDDEN_ROLE", "This operation is not allowed", "business")
    async with read_transaction(
        db,
        settings,
        span_factory=span_factory,
        failure_callback=failure_callback,
    ):
        rows = await repository.history_snapshot(
            db, appointment_id, context.user_id, context.role, query
        )
        if not rows:
            raise _not_found()
        first = rows[0]
        return ProposalHistoryResponse(
            items=[
                ProposalHistoryItem.model_validate(row[5]) for row in rows if row[5] is not None
            ],
            page=query.page,
            page_size=query.page_size,
            total=first[4],
            context=ProposalContext(
                appointment_id=first[0],
                appointment_status=first[1],
                proposal_version=first[2],
                pending_proposal_id=first[3],
            ),
        )


async def _expire_change_proposal_impl(
    db: AsyncSession,
    *,
    proposal_id: UUID,
    settings: Settings,
    request_id: str,
) -> ExpiryTransition:
    """Caller begins/commits/rolls back, bounds the transaction and handles retries.

    Enter with no business locks held. Candidate discovery must be nonlocking; this
    helper acquires settings, ordered accounts, appointment, then proposals. A
    terminal or inconsistent target is never repaired or applied to another proposal.
    SQL failures propagate; caller must roll back. There is no participant eligibility
    requirement for system expiry. Actual mutation stages one audit and two notices.
    """
    if not db.in_transaction():
        raise ValueError("Expiry requires a caller-owned transaction")
    candidate = await db.scalar(
        select(ChangeProposal.appointment_id).where(ChangeProposal.id == proposal_id)
    )
    if candidate is None:
        return "NOT_FOUND"
    locked = await _lock_context(db, candidate, None, settings)
    if locked is None:
        return "NOT_FOUND"
    row = locked.appointment
    proposals = await _lock(repository.lock_proposals(db, row.id, proposal_id), settings)
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        return "NOT_FOUND"
    if proposal.status != "PENDING":
        return "TERMINAL"
    if row.status != "CHANGE_PENDING" or _pending(proposals) is not proposal:
        return "INCONSISTENT"
    now = await users.utc_now(db)
    if now < proposal.expires_at:
        return "NOT_DUE"
    proposal.status, proposal.resolved_at, proposal.resolved_by = "EXPIRED", now, None
    row.status, row.cancelled_at, row.cancelled_by = "CANCELLED", now, None
    row.cancel_reason = "CHANGE_PROPOSAL_EXPIRED"
    _effects(db, row, proposal, "CHANGE_PROPOSAL_EXPIRED", None, request_id)
    return "EXPIRED"


async def expire_change_proposal_in_transaction(
    db: AsyncSession,
    *,
    proposal_id: UUID,
    settings: Settings,
    request_id: str,
    span_factory: SpanFactory | None = None,
    failure_callback: FailureCallback | None = None,
) -> ExpiryTransition:
    """Trace the shared staging boundary without claiming its caller's commit."""
    with safe_span(span_factory, "proposal.expire") as span:
        try:
            result = await _expire_change_proposal_impl(
                db,
                proposal_id=proposal_id,
                settings=settings,
                request_id=request_id,
            )
        except SQLAlchemyError as error:
            if failure_callback is not None:
                try:
                    failure_callback(error)
                except BaseException:
                    pass
            set_business_outcome(span, "failed")
            raise
        except BaseException:
            set_business_outcome(span, "failed")
            raise
        else:
            set_business_outcome(span, "staged" if result == "EXPIRED" else "noop")
            return result
