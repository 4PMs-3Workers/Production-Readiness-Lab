from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.models import Appointment, AuditEvent, ChangeProposal, Notification, User
from app.repositories import appointments, users
from app.schemas.availability import AvailabilityQuery
from app.services.availability import get_availability
from app.worker.models import FailureReason, Outcome, WorkerIdentity
from app.worker.processing import process_candidate

pytestmark = pytest.mark.mysql


async def process(env, identifier):
    request_id = WorkerIdentity().request_id(uuid4(), identifier)
    return await process_candidate(env.factory, identifier, env.settings, request_id)


async def test_due_expiry_atomically_releases_slot_and_quota_with_system_actors(worker_env):
    env = worker_env
    proposal_id, appointment_id = await add_proposal(env)
    query = AvailabilityQuery(
        purpose_id=env.purpose_id,
        staff_id=env.staff_id,
        date=env.start.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date(),
    )

    async def current():
        async with env.factory() as db:
            quota = await appointments.count_blocking_for_student(db, student_id=env.student_id)
            await db.rollback()
            slots = await get_availability(
                db, query, student_id=env.student_id, settings=env.settings, clock=lambda: env.now
            )
        return quota, {slot.starts_at.root for slot in slots.slots}

    quota, slots = await current()
    assert quota == 1 and env.start not in slots  # overdue but not processed still occupies
    result = await process(env, proposal_id)
    assert result.outcome == Outcome.EXPIRED and result.reason is None
    quota, slots = await current()
    assert quota == 0 and env.start in slots
    assert await side_effect_counts(env) == (1, 2)
    async with env.factory() as db:
        proposal = await db.get(ChangeProposal, proposal_id)
        row = await db.get(Appointment, appointment_id)
        assert proposal and row
        assert proposal.status == "EXPIRED" and row.status == "CANCELLED"
        assert (
            proposal.resolved_at == row.cancelled_at and proposal.resolved_at >= proposal.expires_at
        )
        assert proposal.resolved_by is None and row.cancelled_by is None
        assert row.cancel_reason == "CHANGE_PROPOSAL_EXPIRED"
        assert row.starts_at == env.start and row.proposal_version == 1
        audit = (await db.scalars(select(AuditEvent))).one()
        assert audit.actor_id is None and audit.action == "CHANGE_PROPOSAL_EXPIRED"
        assert audit.request_id == result.request_id
        assert audit.event_metadata == {
            "appointment_id": str(appointment_id),
            "proposal_id": str(proposal_id),
        }
        notices = (await db.scalars(select(Notification))).all()
        assert {notice.recipient_id for notice in notices} == {env.student_id, env.staff_id}
        assert {notice.event_type for notice in notices} == {"CHANGE_PROPOSAL_EXPIRED"}
        before = (proposal.resolved_at, row.cancelled_at, row.updated_at)
    repeated = await process(env, proposal_id)
    assert repeated.outcome == Outcome.NOOP and await side_effect_counts(env) == (1, 2)
    async with env.factory() as db:
        proposal = await db.get(ChangeProposal, proposal_id)
        row = await db.get(Appointment, appointment_id)
        assert proposal and row
        assert (proposal.resolved_at, row.cancelled_at, row.updated_at) == before


@pytest.mark.parametrize("offset,outcome", [(0, Outcome.EXPIRED), (1, Outcome.NOOP)])
async def test_expiry_boundary_uses_the_locked_database_clock(
    worker_env, monkeypatch, offset, outcome
):
    env = worker_env
    identifier, _ = await add_proposal(env, expiry=env.now + timedelta(microseconds=offset))

    async def clock(db):
        return env.now

    monkeypatch.setattr(users, "utc_now", clock)
    assert (await process(env, identifier)).outcome == outcome
    assert await side_effect_counts(env) == ((1, 2) if outcome == Outcome.EXPIRED else (0, 0))


@pytest.mark.parametrize("status", ["ACCEPTED", "DECLINED", "EXPIRED", "SUPERSEDED", "CANCELLED"])
async def test_resolved_targets_are_noops_without_mutating_appointment_state(worker_env, status):
    env = worker_env
    identifier, appointment_id = await add_proposal(env, proposal_status=status)
    async with env.factory() as db:
        target = await db.get(ChangeProposal, identifier)
        row = await db.get(Appointment, appointment_id)
        assert target and row
        before = (target.resolved_at, target.resolved_by, row.status, row.updated_at)
    assert (await process(env, identifier)).outcome == Outcome.NOOP
    assert await side_effect_counts(env) == (0, 0)
    async with env.factory() as db:
        target = await db.get(ChangeProposal, identifier)
        row = await db.get(Appointment, appointment_id)
        assert target and row
        assert (target.resolved_at, target.resolved_by, row.status, row.updated_at) == before


@pytest.mark.parametrize("status", ["UPCOMING", "CANCELLED", "COMPLETED", "NO_SHOW"])
async def test_inconsistent_pairs_fail_without_overwriting_terminal_appointments(
    worker_env, status
):
    env = worker_env
    identifier, appointment_id = await add_proposal(env, appointment_status=status)
    result = await process(env, identifier)
    assert result.outcome == Outcome.FAILED and result.reason == FailureReason.INCONSISTENT
    assert await side_effect_counts(env) == (0, 0)
    async with env.factory() as db:
        row, target = await db.get(Appointment, appointment_id), await db.get(
            ChangeProposal, identifier
        )
        assert row and target
        assert row.status == status and target.status == "PENDING" and target.resolved_at is None


async def test_superseded_candidate_never_expires_its_new_pending_successor(worker_env):
    env = worker_env
    identifier, appointment_id = await add_proposal(env, proposal_status="SUPERSEDED")
    async with env.factory.begin() as db:
        row = await db.get(Appointment, appointment_id)
        assert row
        row.proposal_version = 2
        successor = ChangeProposal(
            appointment_id=appointment_id,
            created_by=env.staff_id,
            creation_request_key=uuid4().hex,
            creation_request_hash=bytes(32),
            created_version=2,
            created_at=env.now - timedelta(hours=1),
            proposed_starts_at=env.start + timedelta(hours=2),
            proposed_ends_at=env.start + timedelta(hours=2, minutes=30),
            expires_at=env.now - timedelta(seconds=1),
        )
        db.add(successor)
        await db.flush()
        successor_id = successor.id
    assert (await process(env, identifier)).outcome == Outcome.NOOP
    assert await side_effect_counts(env) == (0, 0)
    async with env.factory() as db:
        successor = await db.get(ChangeProposal, successor_id)
        row = await db.get(Appointment, appointment_id)
        assert successor and row
        assert successor.status == "PENDING" and row.status == "CHANGE_PENDING"


async def test_system_expiry_does_not_need_active_participants_or_an_interactive_session(
    worker_env,
):
    env = worker_env
    identifier, _ = await add_proposal(env)
    async with env.factory.begin() as db:
        for account_id in (env.student_id, env.staff_id):
            row = await db.get(User, account_id)
            assert row
            row.is_active, row.role = False, "ADMIN"
    assert (await process(env, identifier)).outcome == Outcome.EXPIRED
    assert (await process(env, uuid4())).outcome == Outcome.NOOP


async def test_shared_lock_order_and_database_clock_after_all_business_locks(worker_env):
    env = worker_env
    identifier, _ = await add_proposal(env)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(env.engine.sync_engine, "before_cursor_execute", record)
    try:
        assert (await process(env, identifier)).outcome == Outcome.EXPIRED
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", record)
    locks = [text for text in statements if "FOR UPDATE" in text or "LOCK IN SHARE MODE" in text]
    assert "service_settings" in locks[0] and "LOCK IN SHARE MODE" in locks[0]
    assert all("users" in statement for statement in locks[1:3])
    assert "appointments" in locks[3] and "change_proposals" in locks[4]
    assert statements.index("SELECT UTC_TIMESTAMP(6)") > statements.index(locks[-1])
