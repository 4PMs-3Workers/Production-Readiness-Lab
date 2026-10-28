"""Proposal replay upgrades preserve legacy history and guard durable receipts."""

from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError
from tests.backend.integration.test_change_proposals import create, payload
from tests.backend.integration.test_student_cancellation import seed
from tests.backend.integration.test_student_profile_migration import migrate

from app.core.schema_lifecycle import migration_connection
from app.models import Appointment, ChangeProposal


async def test_upgrade_preserves_legacy_pending_history_without_inventing_keys(
    booking_env, auth_settings
):
    env, _, start = booking_env
    identifier = await seed(env, start, "CHANGE_PENDING")
    async with env.factory.begin() as db:
        proposal = ChangeProposal(
            appointment_id=identifier,
            created_by=env.staff_id,
            proposed_starts_at=start,
            proposed_ends_at=start + timedelta(minutes=30),
            proposed_location="Legacy location",
            expires_at=start,
        )
        db.add(proposal)
        await db.flush()
        proposal_id = proposal.id
    engine = env.factory.kw["bind"]
    async with migration_connection(engine) as connection:
        await connection.run_sync(
            lambda db: migrate(db, "20261017_2350_student_profile", downgrade=True)
        )
        await connection.run_sync(lambda db: migrate(db, "head"))
    async with env.factory() as db:
        old = await db.get(ChangeProposal, proposal_id)
        assert old.status == "PENDING" and old.proposed_location == "Legacy location"
        assert old.creation_request_key is old.creation_request_hash is old.created_version is None
        assert (await db.get(Appointment, identifier)).proposal_version == 0
    fresh, _ = await create(
        env, identifier, payload(start, version=0, pending_id=proposal_id), auth_settings
    )
    assert fresh.created_version == 1
    async with env.factory() as db:
        assert (await db.get(ChangeProposal, proposal_id)).status == "SUPERSEDED"


async def test_receipt_cannot_be_downgraded_and_partial_metadata_is_rejected(
    booking_env, auth_settings
):
    env, _, start = booking_env
    identifier = await seed(env, start)
    first, _ = await create(env, identifier, payload(start), auth_settings)
    engine = env.factory.kw["bind"]
    with pytest.raises(RuntimeError, match="durable receipts"):
        async with migration_connection(engine) as connection:
            await connection.run_sync(
                lambda db: migrate(db, "20261017_2350_student_profile", downgrade=True)
            )
    async with env.factory() as db:
        row = await db.get(ChangeProposal, first.id)
        row.creation_request_hash = None
        with pytest.raises(OperationalError) as error:
            await db.flush()
        assert error.value.orig is not None and error.value.orig.args[0] == 3819
        await db.rollback()
    async with env.factory() as db:
        row = await db.get(ChangeProposal, first.id)
        assert row.creation_request_hash is not None and row.created_version == 1
