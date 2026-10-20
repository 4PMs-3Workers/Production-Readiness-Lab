"""Real-lock races, boundary clocks, atomic rollback and ambiguous commit recovery."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_booking_service import _create, _payload
from tests.backend.integration.test_cancellation_concurrency import ordered_race
from tests.backend.integration.test_cancellation_failures import assert_locks_released
from tests.backend.integration.test_change_proposals import counts, create, decide, payload
from tests.backend.integration.test_student_cancellation import cancel, seed

from app.api.errors import AppError
from app.models import Appointment, ChangeProposal, Purpose, User
from app.repositories import users
from app.services import change_proposals as service

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("replacing", [True, False])
async def test_competing_creations_serialize_replay_or_precondition(
    booking_env, auth_settings, monkeypatch, same_key, replacing
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    body = payload(start)
    if replacing:
        previous, _ = await create(env, identifier, body, auth_settings, key="initial")
        body = payload(start + timedelta(minutes=30), version=1, pending_id=previous.id)
    first, second = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        lambda db: create(env, identifier, body, auth_settings, key="first", db=db),
        lambda db: create(
            env, identifier, body, auth_settings, key="first" if same_key else "second", db=db
        ),
    )
    assert not isinstance(first, AppError)
    assert first[1] is False
    if same_key:
        assert second == (first[0], True)
    else:
        assert isinstance(second, AppError) and second.code == "PROPOSAL_PRECONDITION_FAILED"
    assert await counts(env) == ((2, 2, 4) if replacing else (1, 1, 2))


@pytest.mark.parametrize("accept_first", [True, False])
async def test_replacement_and_acceptance_never_apply_to_the_wrong_proposal(
    booking_env, auth_settings, monkeypatch, accept_first
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)

    async def accept(db):
        return await decide(env, identifier, first.id, auth_settings, db=db)

    async def replace(db):
        return await create(
            env,
            identifier,
            payload(start + timedelta(minutes=30), version=1, pending_id=first.id),
            auth_settings,
            key="replacement",
            db=db,
        )

    winner, loser = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        accept if accept_first else replace,
        replace if accept_first else accept,
    )
    assert not isinstance(winner, AppError)
    assert isinstance(loser, AppError)
    assert loser.code == (
        "PROPOSAL_PRECONDITION_FAILED" if accept_first else "PROPOSAL_STATE_CONFLICT"
    )
    assert await counts(env) == ((1 if accept_first else 2), 2, 4)


@pytest.mark.parametrize("decision", ["ACCEPTED", "DECLINED"])
async def test_competing_student_decisions_emit_only_one_resolution(
    booking_env, auth_settings, monkeypatch, decision
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    winner, loser = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        lambda db: decide(env, identifier, first.id, auth_settings, decision=decision, db=db),
        lambda db: decide(
            env,
            identifier,
            first.id,
            auth_settings,
            decision="DECLINED" if decision == "ACCEPTED" else "ACCEPTED",
            db=db,
        ),
    )
    assert not isinstance(winner, AppError)
    assert winner.status == decision
    assert isinstance(loser, AppError) and loser.code == "PROPOSAL_STATE_CONFLICT"
    assert await counts(env) == (1, 2, 4)


@pytest.mark.parametrize("booking_first", [True, False])
async def test_acceptance_and_booking_compete_for_the_unreserved_slot(
    booking_env, auth_settings, monkeypatch, booking_first
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    proposed = start + timedelta(minutes=30)
    first, _ = await create(env, identifier, payload(proposed), auth_settings)

    async def book(db):
        return await _create(
            env, _payload(env, proposed), auth_settings, student_id=env.other_student_id, db=db
        )

    async def accept(db):
        return await decide(env, identifier, first.id, auth_settings, db=db)

    winner, loser = await ordered_race(
        env,
        monkeypatch,
        env.staff_id,
        book if booking_first else accept,
        accept if booking_first else book,
    )
    assert not isinstance(winner, AppError)
    assert isinstance(loser, AppError)
    assert loser.code == ("STAFF_UNAVAILABLE" if booking_first else "BOOKING_CONFLICT")
    async with env.factory() as db:
        row = await db.get(Appointment, identifier)
        assert row.starts_at == (start if booking_first else proposed)
        assert row.status == ("CHANGE_PENDING" if booking_first else "UPCOMING")


async def test_cancellation_closes_pending_before_a_waiting_replacement(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    cancelled, rejected = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        lambda db: cancel(env, identifier, auth_settings, db=db),
        lambda db: create(
            env,
            identifier,
            payload(start + timedelta(minutes=30), version=1, pending_id=first.id),
            auth_settings,
            key="replacement",
            db=db,
        ),
    )
    assert not isinstance(cancelled, AppError)
    assert cancelled.status == "CANCELLED"
    assert isinstance(rejected, AppError) and rejected.code == "PROPOSAL_PRECONDITION_FAILED"
    assert await counts(env) == (1, 2, 4)


@pytest.mark.parametrize("operation", ["accept", "decline", "replace"])
async def test_waiting_operation_reads_database_clock_after_all_locks(
    booking_env, auth_settings, monkeypatch, operation
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    now = first.expires_at.root - timedelta(microseconds=1)
    clock_calls = []

    async def clock(db):
        clock_calls.append(now)
        return now

    monkeypatch.setattr(users, "utc_now", clock)

    async def blocker(db):
        async with db.begin():
            await users.lock_by_id(db, env.student_id)

    async def request(db):
        if operation == "replace":
            return await create(
                env,
                identifier,
                payload(start + timedelta(minutes=30), version=1, pending_id=first.id),
                auth_settings,
                key="replacement",
                db=db,
            )
        return await decide(
            env,
            identifier,
            first.id,
            auth_settings,
            decision="ACCEPTED" if operation == "accept" else "DECLINED",
            db=db,
        )

    def cross_boundary():
        nonlocal now
        assert not clock_calls
        now = first.expires_at.root

    _, rejected = await ordered_race(
        env, monkeypatch, env.student_id, blocker, request, before_release=cross_boundary
    )
    assert isinstance(rejected, AppError) and rejected.code == "PROPOSAL_EXPIRED"
    assert clock_calls == [first.expires_at.root]
    assert await counts(env) == (1, 1, 2)


@pytest.mark.parametrize("stage", ["proposal", "appointment", "audit", "second-notification"])
async def test_failed_replacement_rolls_back_supersede_counter_and_every_side_effect(
    booking_env, auth_settings, stage
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    table, operation = {
        "proposal": ("change_proposals", "INSERT"),
        "appointment": ("appointments", "UPDATE"),
        "audit": ("audit_events", "INSERT"),
        "second-notification": ("notifications", "INSERT"),
    }[stage]
    condition = (
        "IF (SELECT COUNT(*) FROM notifications) >= 3 THEN "
        if stage == "second-notification"
        else ""
    )
    engine = env.factory.kw["bind"]
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE TRIGGER proposal_failure BEFORE {operation} ON {table} FOR EACH ROW BEGIN "
            f"{condition}SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='private injected failure'; "
            f"{'END IF; ' if condition else ''}END"
        )
    try:
        async with env.factory() as db:
            with pytest.raises(AppError) as error:
                await create(
                    env,
                    identifier,
                    payload(start + timedelta(minutes=30), version=1, pending_id=first.id),
                    auth_settings,
                    key="replacement",
                    db=db,
                )
            assert error.value.code == "SERVICE_UNAVAILABLE"
            assert "private" not in error.value.message
            assert not db.in_transaction()
            await assert_locks_released(env, identifier)
        async with env.factory() as db:
            row = await db.get(Appointment, identifier)
            old = await db.get(ChangeProposal, first.id)
            assert row.proposal_version == 1 and row.status == "CHANGE_PENDING"
            assert old.status == "PENDING" and old.resolved_at is None
        assert await counts(env) == (1, 1, 2)
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("DROP TRIGGER proposal_failure")


@pytest.mark.parametrize("committed", [True, False])
@pytest.mark.parametrize("operation", ["create", "decision"])
async def test_unknown_commit_never_retries_writes_and_same_intent_recovers(
    booking_env, auth_settings, monkeypatch, committed, operation
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    body = payload(start)
    first = None
    if operation == "decision":
        first, _ = await create(env, identifier, body, auth_settings)
    original = AsyncSessionTransaction.commit
    calls = 0

    async def uncertain(transaction):
        nonlocal calls
        calls += 1
        if committed:
            await original(transaction)
        raise OperationalError("COMMIT", {}, Exception("transport failed with private content"))

    async def send():
        if first is not None:
            return await decide(env, identifier, first.id, auth_settings)
        return await create(env, identifier, body, auth_settings)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "commit", uncertain)
        with pytest.raises(AppError) as error:
            await send()
        assert error.value.code == "SERVICE_UNAVAILABLE" and calls == 1
    before = (1, 1, 2) if operation == "decision" else (0, 0, 0)
    if committed:
        assert await counts(env) == ((1, 2, 4) if operation == "decision" else (1, 1, 2))
    else:
        assert await counts(env) == before
    recovered = await send()
    if operation == "create":
        assert isinstance(recovered, tuple)
        assert recovered[1] is committed
    else:
        assert not isinstance(recovered, tuple)
        assert recovered.status == "ACCEPTED"
    assert await counts(env) == ((1, 2, 4) if operation == "decision" else (1, 1, 2))


async def test_timeout_after_flush_rolls_back_and_releases_all_locks(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    settings = auth_settings.model_copy(update={"db_query_timeout_seconds": 0.25})
    async with env.factory() as db:
        original = db.flush

        async def flushed_then_blocked(*args, **kwargs):
            has_changes = bool(db.new or db.dirty)
            await original(*args, **kwargs)
            if has_changes:
                with db.no_autoflush:
                    assert await db.scalar(select(ChangeProposal.id)) is not None
                await asyncio.sleep(5)

        monkeypatch.setattr(db, "flush", flushed_then_blocked)
        with pytest.raises(AppError) as error:
            await create(env, identifier, payload(start), settings, db=db)
        assert error.value.code == "SERVICE_UNAVAILABLE" and not db.in_transaction()
        await assert_locks_released(env, identifier)
    assert await counts(env) == (0, 0, 0)


@pytest.mark.parametrize("state", ["due", "not-due", "inconsistent", "terminal"])
async def test_shared_expiry_is_transaction_owned_and_safe_for_inactive_participants(
    booking_env, auth_settings, monkeypatch, state
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    if state == "terminal":
        await cancel(env, identifier, auth_settings)
    async with env.factory.begin() as db:
        (await db.get(User, env.staff_id)).is_active = False
        (await db.get(User, env.student_id)).is_active = False
        (await db.get(Purpose, env.purpose_id)).is_active = False
        if state == "inconsistent":
            row = await db.get(Appointment, identifier)
            row.status, row.cancelled_at = "CANCELLED", start

    async def clock(db):
        return first.expires_at.root - (
            timedelta(microseconds=1) if state == "not-due" else timedelta()
        )

    monkeypatch.setattr(users, "utc_now", clock)

    async def expire(db):
        return await service.expire_change_proposal_in_transaction(
            db,
            proposal_id=first.id,
            settings=auth_settings,
            request_id="expiry-system",
        )

    async with env.factory() as db:
        with pytest.raises(ValueError, match="caller-owned"):
            await expire(db)
    expected = {
        "due": "EXPIRED",
        "not-due": "NOT_DUE",
        "terminal": "TERMINAL",
        "inconsistent": "INCONSISTENT",
    }[state]
    async with env.factory.begin() as db:
        assert await expire(db) == expected
        await db.flush()
    if state == "due":
        async with env.factory.begin() as db:
            assert await expire(db) == "TERMINAL"
        async with env.factory() as db:
            row, proposal = await db.get(Appointment, identifier), await db.get(
                ChangeProposal, first.id
            )
            assert row.status == "CANCELLED" and proposal.status == "EXPIRED"
            assert row.cancelled_by is proposal.resolved_by is None
            assert row.cancelled_at == proposal.resolved_at == first.expires_at.root
            assert row.proposal_version == 1
        assert await counts(env) == (1, 2, 4)
    else:
        assert await counts(env) == ((1, 2, 4) if state == "terminal" else (1, 1, 2))


async def test_expiry_changes_and_effects_rollback_with_caller(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)

    async def clock(db):
        return first.expires_at.root

    monkeypatch.setattr(users, "utc_now", clock)
    async with env.factory() as db:
        await db.begin()
        assert (
            await service.expire_change_proposal_in_transaction(
                db,
                proposal_id=uuid4(),
                settings=auth_settings,
                request_id="missing",
            )
            == "NOT_FOUND"
        )
        assert (
            await service.expire_change_proposal_in_transaction(
                db,
                proposal_id=first.id,
                settings=auth_settings,
                request_id="expiry",
            )
            == "EXPIRED"
        )
        await db.flush()
        await db.rollback()
    assert await counts(env) == (1, 1, 2)
    async with env.factory() as db:
        assert (await db.get(ChangeProposal, first.id)).status == "PENDING"


@pytest.mark.parametrize("error_code", [1205, 1213])
async def test_known_commit_abort_retries_whole_intent_without_duplicate_side_effects(
    booking_env, auth_settings, monkeypatch, error_code
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    original = AsyncSessionTransaction.commit
    calls = 0

    async def abort_once(transaction):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("COMMIT", {}, Exception(error_code, "known transaction abort"))
        await original(transaction)

    monkeypatch.setattr(AsyncSessionTransaction, "commit", abort_once)
    receipt, replayed = await create(env, identifier, payload(start), auth_settings)
    assert calls == 2 and not replayed and receipt.created_version == 1
    assert await counts(env) == (1, 1, 2)


@pytest.mark.parametrize("expiry_first", [True, False])
async def test_worker_and_student_at_exact_expiry_serialize_one_terminal_bundle(
    booking_env, auth_settings, monkeypatch, expiry_first
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)

    async def clock(db):
        return first.expires_at.root

    monkeypatch.setattr(users, "utc_now", clock)

    async def expire(db):
        async with db.begin():
            return await service.expire_change_proposal_in_transaction(
                db, proposal_id=first.id, settings=auth_settings, request_id="expiry-race"
            )

    async def accept(db):
        return await decide(env, identifier, first.id, auth_settings, db=db)

    first_result, second_result = await ordered_race(
        env,
        monkeypatch,
        min(env.student_id, env.staff_id, key=lambda value: value.bytes),
        expire if expiry_first else accept,
        accept if expiry_first else expire,
    )
    expired, rejected = (
        (first_result, second_result) if expiry_first else (second_result, first_result)
    )
    assert expired == "EXPIRED" and isinstance(rejected, AppError)
    assert rejected.code == ("PROPOSAL_STATE_CONFLICT" if expiry_first else "PROPOSAL_EXPIRED")
    assert await counts(env) == (1, 2, 4)
