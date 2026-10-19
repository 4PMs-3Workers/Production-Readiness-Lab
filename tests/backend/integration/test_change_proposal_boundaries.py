"""Proposal eligibility, retained resolutions, scope and cutoff boundaries."""

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.backend.integration.test_booking_service import _seed_appointment
from tests.backend.integration.test_change_proposals import (
    actor,
    counts,
    create,
    decide,
    history,
    payload,
)
from tests.backend.integration.test_student_cancellation import seed

from app.api.errors import AppError
from app.models import Appointment, ChangeProposal, Purpose, StaffPurpose, StaffUnavailability, User
from app.repositories import users

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("change", ["purpose", "acceptance", "staff-role", "unavailability"])
async def test_acceptance_rechecks_changed_eligibility_and_leaves_pending_intact(
    booking_env, auth_settings, change
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    proposed = start + timedelta(minutes=30)
    first, _ = await create(env, identifier, payload(proposed), auth_settings)
    async with env.factory.begin() as db:
        if change == "purpose":
            (await db.get(Purpose, env.purpose_id)).is_active = False
        elif change == "acceptance":
            (await db.get(StaffPurpose, (env.staff_id, env.purpose_id))).is_accepting = False
        elif change == "staff-role":
            (await db.get(User, env.staff_id)).role = "ADMIN"
        else:
            db.add(
                StaffUnavailability(
                    staff_id=env.staff_id,
                    starts_at=proposed,
                    ends_at=proposed + timedelta(minutes=30),
                )
            )
    with pytest.raises(AppError) as error:
        await decide(env, identifier, first.id, auth_settings)
    assert error.value.code == ("STAFF_UNAVAILABLE" if change == "unavailability" else "NOT_FOUND")
    async with env.factory() as db:
        assert (await db.get(Appointment, identifier)).starts_at == start
        assert (await db.get(ChangeProposal, first.id)).status == "PENDING"
    assert await counts(env) == (1, 1, 2)


async def test_acceptance_at_full_quota_and_old_resolution_replay_after_new_proposal(
    booking_env, auth_settings
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    for days in (1, 2):
        await _seed_appointment(
            env,
            student_id=env.student_id,
            staff_id=env.staff_id,
            purpose_id=env.purpose_id,
            starts_at=start + timedelta(days=days),
        )
    first, _ = await create(env, identifier, payload(start), auth_settings)
    accepted = await decide(env, identifier, first.id, auth_settings)
    second, _ = await create(
        env, identifier, payload(start, version=1, location="Room C"), auth_settings, key="second"
    )
    assert await decide(env, identifier, first.id, auth_settings) == accepted
    listing = await history(env, identifier, auth_settings)
    assert (
        listing.context.pending_proposal_id == second.id and listing.context.proposal_version == 2
    )
    assert await counts(env) == (2, 3, 6)


@pytest.mark.parametrize("role", ["STAFF", "STUDENT", "ADMIN"])
async def test_foreign_appointments_and_proposals_are_indistinguishable_from_missing(
    booking_env, auth_settings, role
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    context = actor(env, role, env.other_staff_id if role == "STAFF" else env.other_student_id)
    for appointment_id in (identifier, uuid4()):
        with pytest.raises(AppError) as error:
            await history(env, appointment_id, auth_settings, context=context)
        assert error.value.status_code == (403 if role == "ADMIN" else 404)
    if role == "STAFF":
        with pytest.raises(AppError) as error:
            await create(env, identifier, payload(start), auth_settings, context=context)
        assert error.value.status_code == 404
    if role == "STUDENT":
        with pytest.raises(AppError) as error:
            await decide(env, identifier, first.id, auth_settings, context=context)
        assert error.value.status_code == 404


async def test_foreign_proposal_references_return_scoped_404(booking_env, auth_settings):
    env, _, start = booking_env
    first_appointment, second_appointment = await seed(env, start), await seed(
        env, start + timedelta(days=1)
    )
    first, _ = await create(env, first_appointment, payload(start), auth_settings)
    for proposal_id in (first.id, uuid4()):
        with pytest.raises(AppError) as error:
            await decide(env, second_appointment, proposal_id, auth_settings)
        assert error.value.code == "NOT_FOUND"
        with pytest.raises(AppError) as error:
            await create(
                env, second_appointment, payload(start, pending_id=proposal_id), auth_settings
            )
        assert error.value.code == "NOT_FOUND"


async def test_history_ties_do_not_determine_current_pending_context(booking_env, auth_settings):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    second, _ = await create(
        env,
        identifier,
        payload(start, version=1, pending_id=first.id, location="C"),
        auth_settings,
        key="second",
    )
    async with env.factory.begin() as db:
        (await db.get(ChangeProposal, second.id)).created_at = first.created_at.root
    listing = await history(env, identifier, auth_settings, page_size=1)
    assert listing.items[0].id == max(first.id, second.id, key=lambda value: value.bytes)
    assert listing.context.pending_proposal_id == second.id and listing.total == 2
    next_page = await history(env, identifier, auth_settings, page=2, page_size=1)
    assert next_page.items[0].id != listing.items[0].id and next_page.context == listing.context


@pytest.mark.parametrize("original_later", [True, False])
async def test_expiry_uses_earlier_business_date_across_utc_day_boundaries(
    booking_env, auth_settings, original_later
):
    env, date, start = booking_env
    identifier = await seed(env, start + (timedelta(days=1) if original_later else timedelta()))
    first, _ = await create(env, identifier, payload(start), auth_settings)
    expected = datetime.combine(date, time.min, ZoneInfo("Asia/Ho_Chi_Minh")).astimezone(UTC)
    assert first.expires_at.root == expected and expected.date() < date


async def test_pending_expired_cannot_be_replaced_even_when_new_cutoff_would_be_later(
    booking_env, auth_settings, monkeypatch
):
    env, _, start = booking_env
    identifier = await seed(env, start + timedelta(days=1))
    body = payload(start)
    first, _ = await create(env, identifier, body, auth_settings)

    async def expired(db):
        return first.expires_at.root

    monkeypatch.setattr(users, "utc_now", expired)
    with pytest.raises(AppError) as error:
        await create(
            env,
            identifier,
            payload(start + timedelta(days=1), version=1, pending_id=first.id),
            auth_settings,
            key="extend-deadline",
        )
    assert error.value.code == "PROPOSAL_EXPIRED"
    receipt, replayed = await create(env, identifier, body, auth_settings)
    assert replayed and receipt == first and await counts(env) == (1, 1, 2)
