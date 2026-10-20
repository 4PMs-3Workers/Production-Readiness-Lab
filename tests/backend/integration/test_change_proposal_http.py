"""HTTP boundary guards and a complete proposal lifecycle through the real database."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from tests.backend.integration.test_appointments_api import (
    ORIGIN,
    assert_private,
    install_overrides,
    valid_write_headers,
)
from tests.backend.integration.test_change_proposals import actor, payload
from tests.backend.integration.test_staff_appointment_api import staff_context
from tests.backend.integration.test_student_cancellation import seed

from app.dependencies.auth import require_auth
from app.factory import create_app


@pytest.mark.parametrize("role", [None, "STUDENT", "ADMIN"])
@pytest.mark.parametrize("method", ["get", "post"])
async def test_staff_proposal_routes_require_staff_session(auth_settings, role, method):
    app = create_app(auth_settings)
    context = staff_context(role) if role is not None else None
    install_overrides(app, context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.request(
            method,
            f"/api/v1/staff/me/appointments/{uuid4()}/change-proposals",
            json=payload_wire(),
            headers=valid_write_headers(context) if context else {},
        )
    assert response.status_code == (401 if role is None else 403)
    assert_private(response, error_class="input")


def payload_wire():
    return {
        "expected_proposal_version": 0,
        "expected_pending_proposal_id": None,
        "proposed_starts_at": "2026-10-23T02:00:00Z",
        "proposed_location": "Room A",
    }


@pytest.mark.parametrize("kind,code", [("csrf", "INVALID_CSRF"), ("origin", "INVALID_ORIGIN")])
@pytest.mark.parametrize("decision", [None, "accept", "decline"])
async def test_all_proposal_posts_require_signed_csrf_and_same_origin(
    auth_settings, kind, code, decision
):
    app = create_app(auth_settings)
    context = staff_context("STUDENT" if decision else "STAFF")
    install_overrides(app, context)
    headers = valid_write_headers(
        context, origin="https://attacker.invalid" if kind == "origin" else ORIGIN
    )
    if kind == "csrf":
        headers = {"Origin": ORIGIN, "Idempotency-Key": "key"}
    path = (
        f"/api/v1/appointments/{uuid4()}/change-proposals/{uuid4()}/{decision}"
        if decision
        else (f"/api/v1/staff/me/appointments/{uuid4()}/change-proposals")
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(path, json={} if decision else payload_wire(), headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == code
    assert_private(response, error_class="input")


@pytest.mark.parametrize(
    "kind", ["extra-query", "duplicate-key", "actor", "missing-location", "bad-version"]
)
async def test_creation_rejects_ambiguous_or_owned_inputs(auth_settings, kind):
    app = create_app(auth_settings)
    context = staff_context()
    install_overrides(app, context)
    body, headers = payload_wire(), valid_write_headers(context)
    path = f"/api/v1/staff/me/appointments/{uuid4()}/change-proposals"
    if kind == "extra-query":
        path += "?actor=unexpected"
    if kind == "actor":
        body["actor_id"] = str(context.user_id)
    if kind == "missing-location":
        del body["proposed_location"]
    if kind == "bad-version":
        body["expected_proposal_version"] = True
    wire_headers = list(headers.items())
    if kind == "duplicate-key":
        wire_headers.append(("Idempotency-Key", "other-key"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(path, json=body, headers=wire_headers)
    assert response.status_code == 422
    assert_private(response, error_class="input")


async def test_http_history_create_replay_accept_and_ownership_with_mysql(booking_env):
    env, _, start = booking_env
    identifier = await seed(env, start)
    staff, student = actor(env), actor(env, "STUDENT")
    env.app.dependency_overrides[require_auth] = lambda: staff
    path = f"/api/v1/staff/me/appointments/{identifier}/change-proposals"
    result = await env.client.get(path)
    assert result.status_code == 200
    assert result.json()["context"]["pending_proposal_id"] is None
    body = payload(start + timedelta(minutes=30)).model_dump(mode="json")
    headers = valid_write_headers(staff, key="http-proposal")
    created = await env.client.post(path, json=body, headers=headers)
    assert created.status_code == 201, created.text
    replay = await env.client.post(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == created.json()
    assert_private(replay)
    env.app.dependency_overrides[require_auth] = lambda: student
    student_path = f"/api/v1/appointments/{identifier}/change-proposals"
    listing = await env.client.get(student_path)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["status"] == "PENDING"
    assert (
        "creation_request_key" not in listing.text and "creation_request_hash" not in listing.text
    )
    proposal_id = created.json()["id"]
    accepted = await env.client.post(
        f"{student_path}/{proposal_id}/accept", json={}, headers=valid_write_headers(student)
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"
    again = await env.client.post(
        f"{student_path}/{proposal_id}/accept", json={}, headers=valid_write_headers(student)
    )
    assert again.json() == accepted.json()
    for suffix in ("?page=1&page=2", "?unexpected=true", "?page_size=101"):
        bad = await env.client.get(student_path + suffix)
        assert bad.status_code == 422
    foreign = actor(env, "STUDENT", env.other_student_id)
    env.app.dependency_overrides[require_auth] = lambda: foreign
    for foreign_path in (student_path, f"/api/v1/appointments/{uuid4()}/change-proposals"):
        response = await env.client.get(foreign_path)
        assert response.status_code == 404
        assert_private(response, error_class="input")
