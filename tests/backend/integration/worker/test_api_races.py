from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from tests.backend.integration.test_cancellation_concurrency import ordered_race
from tests.backend.integration.worker.helpers import add_proposal, side_effect_counts

from app.core.csrf import issue_csrf_token
from app.core.session_tokens import hash_session_token, new_session_token
from app.dependencies.database import get_db_session
from app.factory import create_app
from app.models import Appointment, AuditEvent, ChangeProposal, Session
from app.repositories import users
from app.worker.models import CandidateResult, Outcome
from app.worker.processing import process_candidate

pytestmark = pytest.mark.mysql
ORIGIN = "http://localhost:3000"


@asynccontextmanager
async def api_client(env, settings, *, staff):
    token, session_id = new_session_token(), uuid4()
    async with env.factory.begin() as db:
        db.add(
            Session(
                id=session_id,
                user_id=env.staff_id if staff else env.student_id,
                token_hash=hash_session_token(token),
                created_at=env.now - timedelta(seconds=1),
                expires_at=env.now + timedelta(days=1),
            )
        )
    csrf = issue_csrf_token(session_id, settings.csrf_secret.get_secret_value())
    headers = {
        "Cookie": f"prl_session={token}; prl_csrf={csrf}",
        "X-CSRF-Token": csrf,
        "Origin": ORIGIN,
    }
    app = create_app(settings, engine_factory=lambda *args, **kwargs: env.engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=headers
        ) as client:
            yield app, client


async def api_write(client, operation, env, appointment_id, proposal_id):
    if operation in {"accept", "decline"}:
        return await client.post(
            f"/api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/{operation}",
            json={},
        )
    if operation == "replace":
        return await client.post(
            f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals",
            headers={"Idempotency-Key": "worker-race-replacement"},
            json={
                "expected_proposal_version": 1,
                "expected_pending_proposal_id": str(proposal_id),
                "proposed_starts_at": (env.start + timedelta(hours=2)).isoformat(),
                "proposed_location": "Replacement room",
            },
        )
    prefix = "/api/v1/staff/me" if operation == "staff_cancel" else "/api/v1"
    return await client.post(f"{prefix}/appointments/{appointment_id}/cancel", json={})


@pytest.mark.parametrize("worker_first", [True, False])
@pytest.mark.parametrize(
    "operation", ["accept", "decline", "replace", "student_cancel", "staff_cancel"]
)
async def test_expiry_races_real_authenticated_api_operations_in_both_orders(
    worker_env, monkeypatch, worker_first, operation
):
    env = worker_env
    proposal_id, appointment_id = await add_proposal(env, expiry=env.now)
    settings = env.settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )
    api_db = None

    async def clock(db):
        # The winning API evaluates just before expiry; the waiting worker evaluates at expiry.
        if db is api_db and not worker_first:
            return env.now - timedelta(microseconds=1)
        return env.now

    monkeypatch.setattr(users, "utc_now", clock)
    async with api_client(env, settings, staff=operation in {"replace", "staff_cancel"}) as (
        app,
        client,
    ):

        async def session_override():
            assert api_db is not None
            try:
                yield api_db
            finally:
                if api_db.in_transaction():
                    await api_db.rollback()

        app.dependency_overrides[get_db_session] = session_override

        async def request(db):
            nonlocal api_db
            api_db = db
            return await api_write(client, operation, env, appointment_id, proposal_id)

        async def expire(db):
            return await process_candidate(lambda: db, proposal_id, settings, "worker.api-race")

        first, second = await ordered_race(
            env,
            monkeypatch,
            min(env.student_id, env.staff_id, key=lambda value: value.bytes),
            expire if worker_first else request,
            request if worker_first else expire,
        )
    worker_result, response = (first, second) if worker_first else (second, first)
    assert isinstance(worker_result, CandidateResult) and isinstance(response, httpx.Response)
    if worker_first:
        assert worker_result.outcome == Outcome.EXPIRED
        if operation.endswith("cancel"):
            assert response.status_code == 200  # same stored cancellation, without new effects
        else:
            assert response.status_code == 409
            assert response.json()["error"]["code"] == (
                "PROPOSAL_PRECONDITION_FAILED"
                if operation == "replace"
                else "PROPOSAL_STATE_CONFLICT"
            )
    else:
        assert worker_result.outcome == Outcome.NOOP
        assert response.status_code == (201 if operation == "replace" else 200), response.text
    assert await side_effect_counts(env) == (1, 2)
    async with env.factory() as db:
        proposal, row = await db.get(ChangeProposal, proposal_id), await db.get(
            Appointment, appointment_id
        )
        assert proposal and row
        audit = (await db.scalars(select(AuditEvent))).one()
        expected_actor = (
            None
            if worker_first
            else (env.staff_id if operation in {"replace", "staff_cancel"} else env.student_id)
        )
        assert audit.actor_id == expected_actor
        if worker_first:
            assert proposal.status == "EXPIRED" and row.status == "CANCELLED"
            assert row.cancelled_by is None and proposal.resolved_by is None
        elif operation == "replace":
            assert proposal.status == "SUPERSEDED" and row.status == "CHANGE_PENDING"
            pending = (
                await db.scalars(select(ChangeProposal).where(ChangeProposal.status == "PENDING"))
            ).one()
            assert pending.id != proposal_id and row.proposal_version == 2
        elif operation == "accept":
            assert proposal.status == "ACCEPTED" and row.status == "UPCOMING"
            assert row.starts_at == env.start + timedelta(hours=1)
        else:
            assert proposal.status == ("DECLINED" if operation == "decline" else "CANCELLED")
            assert row.status == "CANCELLED" and row.cancelled_by == expected_actor


@pytest.mark.parametrize("operation", ["accept", "decline", "replace"])
async def test_api_waiting_across_cutoff_rejects_then_worker_expires_pending_pair(
    worker_env, monkeypatch, operation
):
    env = worker_env
    proposal_id, appointment_id = await add_proposal(env, expiry=env.now)
    now = env.now - timedelta(microseconds=1)

    async def clock(db):
        return now

    monkeypatch.setattr(users, "utc_now", clock)
    settings = env.settings.model_copy(
        update={
            "request_timeout_seconds": 15,
            "db_query_timeout_seconds": 10,
            "db_lock_timeout_seconds": 5,
        }
    )
    async with api_client(env, settings, staff=operation == "replace") as (app, client):
        api_db = None

        async def session_override():
            assert api_db is not None
            yield api_db

        app.dependency_overrides[get_db_session] = session_override

        async def holder(db):
            async with db.begin():
                await users.lock_by_id(db, env.student_id)

        async def request(db):
            nonlocal api_db
            api_db = db
            return await api_write(client, operation, env, appointment_id, proposal_id)

        def reach_expiry():
            nonlocal now
            now = env.now

        _, response = await ordered_race(
            env, monkeypatch, env.student_id, holder, request, before_release=reach_expiry
        )
    assert isinstance(response, httpx.Response)
    assert response.status_code == 409 and response.json()["error"]["code"] == "PROPOSAL_EXPIRED"
    assert await side_effect_counts(env) == (0, 0)
    assert (
        await process_candidate(env.factory, proposal_id, settings, "worker.after-cutoff")
    ).outcome == Outcome.EXPIRED
    assert await side_effect_counts(env) == (1, 2)
