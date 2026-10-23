"""Real-MySQL coverage for HTTP, business, and database trace relationships."""

import asyncio
import inspect
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSessionTransaction
from tests.backend.integration.test_booking_service import _seed_appointment

from app.domain.auth import AuthContext
from app.models import Appointment, AuditEvent, ChangeProposal, Notification, User
from app.observability.tracing import safe_span
from app.repositories import users
from app.schemas.change_proposals import ProposalCreateRequest
from app.services import change_proposals

pytestmark = pytest.mark.mysql
ORIGIN = "http://localhost:3000"


def _spans(env):
    exporter = InMemorySpanExporter()
    env.app.state.telemetry.trace_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _outcome(span) -> str | None:
    return span.attributes.get("business.outcome")


def _parent(span) -> int | None:
    return span.parent.span_id if span.parent is not None else None


def _assert_safe_database_spans(spans) -> None:
    forbidden_keys = {
        "db.statement",
        "db.query.text",
        "db.parameters",
        "exception.message",
        "exception.stacktrace",
    }
    for span in spans:
        if span.name != "db.query":
            continue
        assert not forbidden_keys.intersection(span.attributes)
        assert not any(key.startswith("exception.") for key in span.attributes)


async def test_business_span_exit_failure_does_not_suppress_cancellation():
    @contextmanager
    def throwing_span(_name):
        try:
            yield None
        finally:
            raise RuntimeError("telemetry span exit failure")

    async def cancelled_operation():
        with safe_span(throwing_span, "booking.create"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled_operation()


async def _login_student(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.invalid", "password": "  Good-password!  "},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


async def test_http_booking_span_has_business_and_database_children_and_replay_is_distinct(
    booking_env,
):
    env, _, starts_at = booking_env
    exporter = _spans(env)
    await _login_student(env.client)
    body = {
        "purpose_id": str(env.purpose_id),
        "staff_id": str(env.staff_id),
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
    }
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": env.client.cookies.get("prl_csrf"),
        "Idempotency-Key": "trace-booking-replay",
    }
    created = await env.client.post("/api/v1/appointments", json=body, headers=headers)
    replay = await env.client.post("/api/v1/appointments", json=body, headers=headers)
    assert created.status_code == 201 and replay.status_code == 200

    spans = exporter.get_finished_spans()
    roots = {
        span.context.span_id: span
        for span in spans
        if span.name == "http.server" and span.parent is None and span.context is not None
    }
    businesses = [span for span in spans if span.name == "booking.create"]
    assert len(businesses) == 2
    outcomes = {_outcome(span) for span in businesses}
    assert outcomes == {"committed", "replayed"}
    for business in businesses:
        assert business.context is not None
        business_span_id = business.context.span_id
        assert _parent(business) in roots
        children = [span for span in spans if _parent(span) == business_span_id]
        assert any(span.name == "db.query" for span in children)
        if _outcome(business) == "committed":
            assert any(span.name == "db.commit" for span in children)
        else:
            assert not any(span.name == "db.commit" for span in children)
    _assert_safe_database_spans(spans)


@pytest.mark.parametrize("server_committed", [False, True])
async def test_unknown_booking_commit_is_never_annotated_as_committed(
    booking_env, monkeypatch, server_committed
):
    env, _, starts_at = booking_env
    exporter = _spans(env)
    await _login_student(env.client)
    original = AsyncSessionTransaction.commit
    calls = 0

    async def uncertain(transaction):
        nonlocal calls
        calls += 1
        if server_committed:
            await original(transaction)
        raise OperationalError("COMMIT", {}, Exception("private transport detail"))

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "commit", uncertain)
        response = await env.client.post(
            "/api/v1/appointments",
            json={
                "purpose_id": str(env.purpose_id),
                "staff_id": str(env.staff_id),
                "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
            },
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": env.client.cookies.get("prl_csrf"),
                "Idempotency-Key": "trace-unknown-commit",
            },
        )
    assert response.status_code == 503 and calls == 1
    spans = exporter.get_finished_spans()
    booking = [span for span in spans if span.name == "booking.create"]
    assert len(booking) == 1
    assert _outcome(booking[0]) == "commit_unknown"
    assert _outcome(booking[0]) != "committed"
    _assert_safe_database_spans(spans)

    async with env.factory() as db:
        counts = tuple(
            [
                int(await db.scalar(select(func.count()).select_from(model)) or 0)
                for model in (Appointment, AuditEvent, Notification)
            ]
        )
    assert counts == ((1, 1, 2) if server_committed else (0, 0, 0))


async def test_proposal_create_accept_noop_and_decline_have_safe_business_outcomes(booking_env):
    env, _, starts_at = booking_env
    exporter = _spans(env)
    await _login_student(env.client)
    async with env.factory.begin() as db:
        student = await db.get(User, env.student_id)
        staff = await db.get(User, env.staff_id)
        assert student is not None and staff is not None
        staff.password_hash = student.password_hash

    student_headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": env.client.cookies.get("prl_csrf"),
    }
    booking = await env.client.post(
        "/api/v1/appointments",
        json={
            "purpose_id": str(env.purpose_id),
            "staff_id": str(env.staff_id),
            "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        },
        headers={**student_headers, "Idempotency-Key": "trace-proposal-booking"},
    )
    assert booking.status_code == 201
    appointment_id = booking.json()["id"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN
    ) as staff_client:
        login = await staff_client.post(
            "/api/v1/auth/login",
            json={"email": "staff@example.invalid", "password": "  Good-password!  "},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        staff_headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": staff_client.cookies.get("prl_csrf"),
        }
        create_path = f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals"
        first_body = {
            "expected_proposal_version": 0,
            "expected_pending_proposal_id": None,
            "proposed_starts_at": (starts_at + timedelta(minutes=30))
            .isoformat()
            .replace("+00:00", "Z"),
            "proposed_location": "Room B",
        }
        first = await staff_client.post(
            create_path,
            json=first_body,
            headers={**staff_headers, "Idempotency-Key": "trace-proposal-first"},
        )
        replay = await staff_client.post(
            create_path,
            json=first_body,
            headers={**staff_headers, "Idempotency-Key": "trace-proposal-first"},
        )
        assert first.status_code == 201 and replay.status_code == 200
        proposal_id = first.json()["id"]
        accept_path = f"/api/v1/appointments/{appointment_id}/change-proposals/{proposal_id}/accept"
        accepted = await env.client.post(accept_path, json={}, headers=student_headers)
        repeated_accept = await env.client.post(accept_path, json={}, headers=student_headers)
        assert accepted.status_code == repeated_accept.status_code == 200

        second_body = {
            "expected_proposal_version": 1,
            "expected_pending_proposal_id": None,
            "proposed_starts_at": (starts_at + timedelta(minutes=60))
            .isoformat()
            .replace("+00:00", "Z"),
            "proposed_location": "Room C",
        }
        second = await staff_client.post(
            create_path,
            json=second_body,
            headers={**staff_headers, "Idempotency-Key": "trace-proposal-second"},
        )
        assert second.status_code == 201
        decline_path = (
            f"/api/v1/appointments/{appointment_id}/change-proposals/{second.json()['id']}/decline"
        )
        declined = await env.client.post(decline_path, json={}, headers=student_headers)
        assert declined.status_code == 200

    spans = exporter.get_finished_spans()
    business = [
        span
        for span in spans
        if span.name in {"proposal.create", "proposal.accept", "proposal.decline"}
    ]
    assert [_outcome(span) for span in business].count("committed") == 4
    assert [_outcome(span) for span in business].count("replayed") == 1
    assert [_outcome(span) for span in business].count("noop") == 1
    roots = {
        span.context.span_id
        for span in spans
        if span.name == "http.server" and span.parent is None and span.context is not None
    }
    assert all(_parent(span) in roots for span in business)
    _assert_safe_database_spans(spans)


async def test_expiry_trace_says_staged_when_caller_rolls_back(
    booking_env, auth_settings, monkeypatch
):
    env, _, starts_at = booking_env
    exporter = _spans(env)
    appointment_id = await _seed_appointment(
        env,
        student_id=env.student_id,
        staff_id=env.staff_id,
        purpose_id=env.purpose_id,
        starts_at=starts_at,
    )
    proposal_payload = ProposalCreateRequest(
        expected_proposal_version=0,
        expected_pending_proposal_id=None,
        proposed_starts_at=starts_at + timedelta(minutes=30),
        proposed_location="Room B",
    )
    context = AuthContext(
        session_id=uuid4(),
        user_id=env.staff_id,
        email="staff@example.invalid",
        name="Selected Staff",
        role="STAFF",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with env.factory() as db:
        proposal, replayed = await change_proposals.create_proposal(
            db,
            appointment_id=appointment_id,
            payload=proposal_payload,
            context=context,
            idempotency_key="trace-expiry-setup",
            request_id="trace-expiry-setup",
            settings=auth_settings,
            deadline=asyncio.get_running_loop().time() + 10,
        )
    assert not replayed

    parameters = inspect.signature(
        change_proposals.expire_change_proposal_in_transaction
    ).parameters
    assert "span_factory" in parameters

    async def at_expiry(_db):
        return proposal.expires_at.root

    monkeypatch.setattr(users, "utc_now", at_expiry)
    async with env.factory() as db:
        await db.begin()
        result = await change_proposals.expire_change_proposal_in_transaction(
            db,
            proposal_id=proposal.id,
            settings=auth_settings,
            request_id="trace-expiry-staged",
            span_factory=env.app.state.telemetry.span,
        )
        assert result == "EXPIRED"
        await db.flush()
        await db.rollback()

    spans = exporter.get_finished_spans()
    expiry = [span for span in spans if span.name == "proposal.expire"]
    assert len(expiry) == 1 and _outcome(expiry[0]) == "staged"
    async with env.factory() as db:
        stored = await db.get(ChangeProposal, proposal.id)
        assert stored is not None and stored.status == "PENDING"
