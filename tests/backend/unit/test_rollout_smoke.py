from datetime import date, timedelta
from uuid import UUID

import httpx
import pytest
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_runtime import Deadline
from tools.deploy.rollout_smoke import booking_payload, business_smoke
from tools.deploy.rollout_state import SMOKE_CHECKS, RolloutError


@pytest.mark.parametrize(
    ("horizon", "has_slot", "queried_days"),
    [(368, True, 1), (368, False, 367), (1, False, 1), (0, False, 0)],
)
def test_smoke_searches_only_a_bounded_prefix_of_the_booking_horizon(
    horizon, has_slot, queried_days
):
    purpose_id = "11111111-1111-1111-1111-111111111111"
    staff_id = "22222222-2222-2222-2222-222222222222"
    first = date(2026, 11, 6)
    last = first + timedelta(days=horizon - 1)
    start = "2026-11-06T09:00:00Z"
    dates = []

    def handler(request):
        path = request.url.path
        if path == "/api/v1/booking-context":
            body = {"min_booking_date": str(first), "max_booking_date": str(last)}
        elif path == "/api/v1/purposes":
            body = {
                "items": [{"id": purpose_id, "code": "CAREER", "requires_free_text": False}],
                "total": 1,
            }
        elif path == "/api/v1/staff":
            body = {"items": [{"id": staff_id}]}
        else:
            assert path == "/api/v1/availability"
            dates.append(date.fromisoformat(request.url.params["date"]))
            body = {"slots": [{"starts_at": start}] if has_slot else []}
        return httpx.Response(
            200, json=body, headers={"Cache-Control": "private, no-store", "X-Request-ID": "r"}
        )

    with SmokeSession(
        "http://localhost:8080", Deadline(30), transport=httpx.MockTransport(handler)
    ) as session:
        account = SmokeCredentials("private@example.invalid", "private-password", "CAREER")
        if has_slot:
            assert booking_payload(session, account) == {
                "purpose_id": purpose_id,
                "staff_id": staff_id,
                "starts_at": start,
            }
        else:
            with pytest.raises(RolloutError, match="smoke-failed"):
                booking_payload(session, account)
    assert dates == [first + timedelta(days=offset) for offset in range(queried_days)]


@pytest.mark.parametrize("fault", [None, "replay", "effects", "logout", "purpose-missing"])
def test_complete_gateway_smoke_and_failed_invariants(fault):
    created, cancelled, logged_out = [0], [0], [False]
    appointment = "33333333-3333-3333-3333-333333333333"
    purpose_pages = []

    def handler(request):
        path = request.url.path
        body, status = {}, 200
        if path == "/login":
            pass
        elif path.endswith("auth/login"):
            pass
        elif path.endswith("auth/csrf"):
            body = dict(csrf_token="signed-token")
        elif path == "/api/v1/booking-context":
            body = dict(min_booking_date="2026-11-06", max_booking_date="2026-11-07")
        elif path == "/api/v1/purposes":
            page = int(request.url.params["page"])
            assert request.url.params["page_size"] == "50"
            purpose_pages.append(page)
            if page == 1:
                items = [
                    dict(id=str(UUID(int=index + 1)), code=f"A{index:02}", requires_free_text=False)
                    for index in range(50)
                ]
            else:
                assert page == 2
                items = [
                    dict(
                        id="11111111-1111-1111-1111-111111111111",
                        code="OTHER" if fault == "purpose-missing" else "CAREER",
                        requires_free_text=False,
                    )
                ]
            body = dict(items=items, total=51)
        elif path == "/api/v1/staff":
            assert request.url.params["purpose_id"] == "11111111-1111-1111-1111-111111111111"
            body = dict(items=[dict(id="22222222-2222-2222-2222-222222222222")])
        elif path == "/api/v1/availability":
            body = dict(slots=[dict(starts_at="2026-11-06T09:00:00Z")])
        elif path.endswith("auth/logout"):
            logged_out[0] = True
            status = 204
        elif path.endswith("/cancel"):
            cancelled[0] += 1
            body = dict(id=appointment, status="CANCELLED", cancelled_at="2026-11-05T00:01:00Z")
        elif path == "/api/v1/appointments" and request.method == "POST":
            created[0] += 1
            status = 201 if created[0] == 1 else 200
            body = dict(
                id=appointment if created[0] == 1 or fault != "replay" else "changed-id",
                status="UPCOMING",
                created_at="2026-11-05T00:00:00Z",
            )
        elif path == "/api/v1/appointments":
            status = 200 if not logged_out[0] or fault == "logout" else 401
            body = dict(items=[])
        elif path.endswith(appointment):
            body = dict(id=appointment, status="CANCELLED" if cancelled[0] else "UPCOMING")
            if cancelled[0]:
                body["cancelled_at"] = "2026-11-05T00:01:00Z"
        else:
            raise AssertionError("Unexpected smoke route")
        return httpx.Response(
            status,
            json=body if status != 204 else None,
            headers={"Cache-Control": "private, no-store", "X-Request-ID": "r"},
        )

    def effects(identifier):
        assert identifier == appointment
        if fault == "effects":
            return (1, 2, 2)
        return (1, 2, 4) if cancelled[0] else (1, 1, 2)

    with SmokeSession(
        "http://localhost:8080", Deadline(30), transport=httpx.MockTransport(handler)
    ) as session:
        account = SmokeCredentials("private@example.invalid", "private-password", "CAREER")
        if fault is not None:
            with pytest.raises(RolloutError, match="smoke-failed"):
                business_smoke(session, account, effects)
        else:
            assert business_smoke(session, account, effects) == sorted(SMOKE_CHECKS)
            assert (created[0], cancelled[0], logged_out[0]) == (2, 2, True)
    assert purpose_pages == [1, 2]
    if fault == "purpose-missing":
        assert created[0] == 0
