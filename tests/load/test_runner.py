import asyncio
import json
import random
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from tools.load.runner import (
    WorkloadResult,
    _authenticate,
    _book,
    _choose_operation,
    _http_outcome,
    _propose,
    _Session,
    confirm_fixture_target,
    load_workload_fixture,
)

_TEST_PASSWORD = "private-fictional-load-session-password-53"


def test_operation_mix_is_role_specific_and_representative():
    assert _choose_operation("STUDENT", 0.1) == "read"
    assert _choose_operation("STUDENT", 0.6) == "book"
    assert _choose_operation("STUDENT", 0.9) == "cancel"
    assert _choose_operation("STAFF", 0.1) == "read"
    assert _choose_operation("STAFF", 0.9) == "proposal"
    assert _http_outcome(429, expected={200}) == "capacity_rejection"


def test_login_refreshes_csrf_and_uses_the_matching_session_identity():
    fixture = load_workload_fixture(55, "b" * 32, _TEST_PASSWORD)
    credential = fixture.students[0]

    def handle(request):
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={"id": str(credential.user_id)},
                headers={"Set-Cookie": "prl_session=fake-session; Path=/; HttpOnly"},
            )
        assert request.url.path.endswith("/auth/csrf")
        return httpx.Response(
            200,
            json={"csrf_token": "signed-csrf", "user_id": str(credential.user_id)},
            headers={"Set-Cookie": "prl_csrf=signed-csrf; Path=/"},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://candidate.invalid"
        ) as client:
            session = await _authenticate(client, credential, fixture.password)
            assert session.csrf_token == "signed-csrf"
            assert client.headers["X-CSRF-Token"] == "signed-csrf"
            assert client.cookies.get("prl_session") == "fake-session"
            assert client.cookies.get("prl_csrf") == "signed-csrf"

    asyncio.run(run())


def test_fixture_account_email_is_unique_per_run_id():
    first = load_workload_fixture(55, "b" * 32, _TEST_PASSWORD)
    second = load_workload_fixture(55, "c" * 32, _TEST_PASSWORD)
    assert first.students[0].email != second.students[0].email
    assert first.password == second.password == _TEST_PASSWORD
    assert _TEST_PASSWORD not in repr(first)


def test_canary_mismatch_fails_before_any_load_write():
    requests = []
    fixture = load_workload_fixture(55, "b" * 32, _TEST_PASSWORD)

    def handle(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": {"code": "INVALID_CREDENTIALS"}})

    async def run():
        def client_factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

        with pytest.raises(RuntimeError, match="rejected"):
            await confirm_fixture_target(
                "https://candidate.invalid", fixture, client_factory=client_factory
            )

    asyncio.run(run())
    assert requests == [("POST", "/api/v1/auth/login")]


def test_booking_uses_real_csrf_header_and_replays_same_key_and_payload():
    fixture = load_workload_fixture(88, "c" * 32, _TEST_PASSWORD)
    credential = fixture.students[0]
    writes = []
    receipt_id = "d" * 32

    def handle(request):
        if request.url.path.endswith("/availability"):
            return httpx.Response(
                200,
                json={
                    "slots": [
                        {"starts_at": "2026-11-10T03:00:00Z", "ends_at": "2026-11-10T03:30:00Z"}
                    ]
                },
            )
        assert request.headers["X-CSRF-Token"] == "signed-csrf"
        writes.append((request.headers["Idempotency-Key"], request.content))
        return httpx.Response(201 if len(writes) == 1 else 200, json={"id": receipt_id})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="https://candidate.invalid",
            headers={"X-CSRF-Token": "signed-csrf"},
        ) as client:
            session = _Session(credential, client, "signed-csrf")
            assert await _book(session, fixture, __import__("random").Random(1)) == "success"
            assert session.appointments == [UUID(receipt_id)]

    asyncio.run(run())
    assert len(writes) == 2
    assert writes[0][0] == writes[1][0]
    assert json.loads(writes[0][1]) == json.loads(writes[1][1])


def test_booking_replay_mismatch_remains_in_invariant_inventory():
    fixture = load_workload_fixture(89, "d" * 32, _TEST_PASSWORD)
    credential = fixture.students[0]
    writes = 0
    created_id = UUID("d" * 32)

    def handle(request):
        nonlocal writes
        if request.url.path.endswith("/availability"):
            return httpx.Response(200, json={"slots": [{"starts_at": "2026-11-10T03:00:00Z"}]})
        writes += 1
        receipt = created_id if writes == 1 else UUID("e" * 32)
        return httpx.Response(201 if writes == 1 else 200, json={"id": str(receipt)})

    async def run():
        result = WorkloadResult(1, 1, 1, datetime.now(UTC), datetime.now(UTC))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="https://candidate.invalid",
            headers={"X-CSRF-Token": "signed-csrf"},
        ) as client:
            session = _Session(credential, client, "signed-csrf")
            outcome = await _book(session, fixture, random.Random(1), result)
        return result, session, outcome

    result, session, outcome = asyncio.run(run())
    assert outcome == "idempotency_replay_failed"
    assert result.created_booking_ids == [created_id]
    assert result.idempotency_failures == 1
    assert result.verified_replay_count == 0
    assert session.appointments == [created_id]


def test_proposal_uses_expected_context_csrf_and_exact_replay():
    fixture = load_workload_fixture(42, "e" * 32, _TEST_PASSWORD)
    credential = fixture.staff[0]
    proposal_id = "f" * 32
    writes = []

    def handle(request):
        if request.method == "GET" and request.url.path.endswith("change-proposals"):
            return httpx.Response(
                200,
                json={
                    "context": {
                        "proposal_version": 1,
                        "pending_proposal_id": "1" * 32,
                    }
                },
            )
        if request.url.path.endswith("/availability"):
            return httpx.Response(
                200,
                json={"slots": [{"starts_at": "2026-11-09T03:00:00Z"}]},
            )
        assert request.headers["X-CSRF-Token"] == "signed-csrf"
        writes.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        return httpx.Response(
            201 if len(writes) == 1 else 200,
            json={"id": proposal_id},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="https://candidate.invalid",
            headers={"X-CSRF-Token": "signed-csrf"},
        ) as client:
            session = _Session(credential, client, "signed-csrf")
            assert await _propose(session, fixture, __import__("random").Random(1)) == "success"

    asyncio.run(run())
    assert len(writes) == 2
    assert writes[0] == writes[1]
    assert writes[0][1]["expected_proposal_version"] == 1
    assert writes[0][1]["expected_pending_proposal_id"] == "1" * 32
