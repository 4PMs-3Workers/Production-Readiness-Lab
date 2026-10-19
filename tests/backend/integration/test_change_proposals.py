"""Alembic-backed proposal lifecycle, replay, isolation and exact side effects."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from tests.backend.integration.test_booking_service import _context, _seed_appointment
from tests.backend.integration.test_student_cancellation import cancel, seed

from app.api.errors import AppError
from app.models import (
    Appointment,
    AuditEvent,
    ChangeProposal,
    Notification,
    Purpose,
    StaffPurpose,
    User,
)
from app.schemas.change_proposals import ProposalCreateRequest, ProposalHistoryQuery
from app.services import change_proposals as service

pytestmark = pytest.mark.mysql


def actor(env, role="STAFF", user_id=None):
    identifier = user_id or (env.staff_id if role == "STAFF" else env.student_id)
    return replace(_context(identifier), role=role)


def payload(start, *, version=0, pending_id=None, location: str | None = "Room B"):
    return ProposalCreateRequest(
        expected_proposal_version=version,
        expected_pending_proposal_id=pending_id,
        proposed_starts_at=start,
        proposed_location=location,
    )


async def create(env, appointment_id, body, settings, *, key="proposal-key", db=None, context=None):
    if db is None:
        async with env.factory() as session:
            return await create(
                env, appointment_id, body, settings, key=key, db=session, context=context
            )
    return await service.create_proposal(
        db,
        appointment_id=appointment_id,
        payload=body,
        context=context or actor(env),
        idempotency_key=key,
        request_id="proposal-request",
        settings=settings,
        deadline=asyncio.get_running_loop().time() + 10,
    )


async def decide(
    env,
    appointment_id,
    proposal_id,
    settings,
    *,
    decision: service.Decision = "ACCEPTED",
    db=None,
    context=None,
):
    if db is None:
        async with env.factory() as session:
            return await decide(
                env,
                appointment_id,
                proposal_id,
                settings,
                decision=decision,
                db=session,
                context=context,
            )
    return await service.decide_proposal(
        db,
        appointment_id=appointment_id,
        proposal_id=proposal_id,
        decision=decision,
        context=context or actor(env, "STUDENT"),
        request_id="decision-request",
        settings=settings,
        deadline=asyncio.get_running_loop().time() + 10,
    )


async def history(env, appointment_id, settings, *, page=1, page_size=20, context=None):
    async with env.factory() as db:
        return await service.list_proposals(
            db,
            appointment_id=appointment_id,
            context=context or actor(env),
            settings=settings,
            query=ProposalHistoryQuery(page=page, page_size=page_size),
        )


async def counts(env):
    async with env.factory() as db:
        return tuple(
            [
                int(await db.scalar(select(func.count()).select_from(model)) or 0)
                for model in (ChangeProposal, AuditEvent, Notification)
            ]
        )


async def test_replace_and_old_replay_never_supersede_a_newer_proposal(booking_env, auth_settings):
    env, date, start = booking_env
    identifier = await seed(env, start)
    body = payload(start + timedelta(minutes=30))
    first, replayed = await create(env, identifier, body, auth_settings)
    assert not replayed and first.created_version == 1
    second, _ = await create(
        env,
        identifier,
        payload(start + timedelta(hours=1), version=1, pending_id=first.id),
        auth_settings,
        key="replacement",
    )
    replay, replayed = await create(env, identifier, body, auth_settings)
    assert replayed and replay == first
    assert await counts(env) == (2, 2, 4)
    async with env.factory() as db:
        row = await db.get(Appointment, identifier)
        assert (row.status, row.starts_at, row.location, row.proposal_version) == (
            "CHANGE_PENDING",
            start,
            None,
            2,
        )
        p1, p2 = await db.get(ChangeProposal, first.id), await db.get(ChangeProposal, second.id)
        assert (p1.status, p1.resolved_by, p2.status) == ("SUPERSEDED", env.staff_id, "PENDING")
        assert p1.expires_at == datetime.combine(
            date, time.min, ZoneInfo("Asia/Ho_Chi_Minh")
        ).astimezone(UTC)
    listing = await history(env, identifier, auth_settings)
    assert (
        listing.context.pending_proposal_id == second.id and listing.context.proposal_version == 2
    )
    assert [item.id for item in listing.items] == [second.id, first.id]
    empty = await history(env, identifier, auth_settings, page=20, page_size=1)
    assert not empty.items and empty.total == 2 and empty.context == listing.context


async def test_both_preconditions_and_hash_are_enforced_after_acceptance(
    booking_env, auth_settings
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    body = payload(start + timedelta(minutes=30))
    first, _ = await create(env, identifier, body, auth_settings)
    accepted = await decide(env, identifier, first.id, auth_settings)
    for stale in (payload(start, version=0), payload(start, version=1, pending_id=first.id)):
        with pytest.raises(AppError, match="conflicts") as error:
            await create(env, identifier, stale, auth_settings, key=uuid4().hex)
        assert error.value.code == "PROPOSAL_PRECONDITION_FAILED"
    with pytest.raises(AppError) as error:
        await create(env, identifier, payload(start), auth_settings)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    await cancel(env, identifier, auth_settings)
    replay, replayed = await create(env, identifier, body, auth_settings)
    assert replayed and replay == first
    assert await decide(env, identifier, first.id, auth_settings) == accepted
    with pytest.raises(AppError) as error:
        await decide(env, identifier, first.id, auth_settings, decision="DECLINED")
    assert error.value.code == "PROPOSAL_STATE_CONFLICT"
    assert await counts(env) == (1, 3, 6)


async def test_location_only_preserves_stored_duration_and_excludes_own_slot(
    booking_env, auth_settings
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    async with env.factory.begin() as db:
        (await db.get(Purpose, env.purpose_id)).duration_minutes = 60
    first, _ = await create(env, identifier, payload(start), auth_settings)
    await decide(env, identifier, first.id, auth_settings)
    async with env.factory() as db:
        row = await db.get(Appointment, identifier)
        assert (
            row.starts_at,
            row.ends_at,
            row.duration_minutes,
            row.location,
            row.proposal_version,
        ) == (
            start,
            start + timedelta(minutes=30),
            30,
            "Room B",
            1,
        )


@pytest.mark.parametrize("kind", ["staff", "student", "purpose", "acceptance", "no-change"])
async def test_creation_rejects_ineligible_or_unchanged_proposals_without_side_effects(
    booking_env, auth_settings, kind
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    async with env.factory.begin() as db:
        if kind in {"staff", "student"}:
            (await db.get(User, env.staff_id if kind == "staff" else env.student_id)).is_active = (
                False
            )
        if kind == "purpose":
            (await db.get(Purpose, env.purpose_id)).is_active = False
        if kind == "acceptance":
            (await db.get(StaffPurpose, (env.staff_id, env.purpose_id))).is_accepting = False
    with pytest.raises(AppError) as error:
        await create(
            env,
            identifier,
            payload(start, location=None if kind == "no-change" else "B"),
            auth_settings,
        )
    assert error.value.code == ("PROPOSAL_NO_CHANGE" if kind == "no-change" else "NOT_FOUND")
    assert await counts(env) == (0, 0, 0)


@pytest.mark.parametrize("conflicting", ["staff", "student"])
async def test_acceptance_revalidates_unreserved_slot_and_preserves_original(
    booking_env, auth_settings, conflicting
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    proposed = start + timedelta(minutes=30)
    first, _ = await create(env, identifier, payload(proposed), auth_settings)
    await _seed_appointment(
        env,
        student_id=env.other_student_id if conflicting == "staff" else env.student_id,
        staff_id=env.staff_id if conflicting == "staff" else env.other_staff_id,
        purpose_id=env.purpose_id,
        starts_at=proposed,
    )
    with pytest.raises(AppError) as error:
        await decide(env, identifier, first.id, auth_settings)
    assert error.value.code == (
        "STAFF_UNAVAILABLE" if conflicting == "staff" else "STUDENT_UNAVAILABLE"
    )
    async with env.factory() as db:
        row = await db.get(Appointment, identifier)
        assert row.starts_at == start and row.status == "CHANGE_PENDING"
        assert (await db.get(ChangeProposal, first.id)).status == "PENDING"
    assert await counts(env) == (1, 1, 2)


async def test_decline_with_inactive_staff_and_catalog_replays_after_deadline(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    async with env.factory.begin() as db:
        (await db.get(User, env.staff_id)).is_active = False
        (await db.get(Purpose, env.purpose_id)).is_active = False
        (await db.get(StaffPurpose, (env.staff_id, env.purpose_id))).is_accepting = False
    result = await decide(env, identifier, first.id, auth_settings, decision="DECLINED")

    async def late(db):
        return first.expires_at.root + timedelta(days=2)

    monkeypatch.setattr(service.users, "utc_now", late)
    assert await decide(env, identifier, first.id, auth_settings, decision="DECLINED") == result
    async with env.factory() as db:
        row = await db.get(Appointment, identifier)
        assert row.status == "CANCELLED" and row.cancelled_by == env.student_id
        assert row.cancelled_at == result.resolved_at.root
    assert await counts(env) == (1, 2, 4)
