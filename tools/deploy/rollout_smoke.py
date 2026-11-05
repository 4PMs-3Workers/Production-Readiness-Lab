"""Gateway business verification with private receipts and exact replay effects."""

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID, uuid4

from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_state import SMOKE_CHECKS, RolloutError

Effects = Callable[[str], tuple[int, int, int]]


def booking_payload(session: SmokeSession, account: SmokeCredentials) -> dict:
    context = session.request("GET", "/api/v1/booking-context", 200, private=True).json()
    purposes = session.request(
        "GET", "/api/v1/purposes?page=1&page_size=50", 200, private=True
    ).json()
    purpose = next(
        p
        for p in purposes["items"]
        if p["code"] == account.purpose_code and p["requires_free_text"] is False
    )
    purpose_id = str(UUID(purpose["id"]))
    staff = session.request(
        "GET",
        "/api/v1/staff",
        200,
        private=True,
        params=dict(purpose_id=purpose_id, page=1, page_size=50),
    ).json()["items"]
    if account.staff_id is not None:
        staff = [p for p in staff if p["id"] == account.staff_id]
    selected = date.fromisoformat(context["min_booking_date"])
    last = date.fromisoformat(context["max_booking_date"])
    if last < selected or (last - selected).days > 366:
        raise RolloutError("smoke-failed")
    while selected <= last:
        for member in staff:
            staff_id = str(UUID(member["id"]))
            slots = session.request(
                "GET",
                "/api/v1/availability",
                200,
                private=True,
                params=dict(purpose_id=purpose_id, staff_id=staff_id, date=selected.isoformat()),
            ).json()["slots"]
            if slots:
                return dict(
                    purpose_id=purpose_id, staff_id=staff_id, starts_at=slots[0]["starts_at"]
                )
        selected += timedelta(days=1)
    raise RolloutError("smoke-failed")


def business_smoke(session: SmokeSession, account: SmokeCredentials, effects: Effects) -> list[str]:
    try:
        session.request("GET", "/login", 200)
        session.login(account)
        session.request("GET", "/api/v1/appointments?page=1&page_size=50", 200, private=True)
        payload, key = booking_payload(session, account), uuid4().hex
        receipt = session.request(
            "POST", "/api/v1/appointments", 201, key=key, private=True, json=payload
        ).json()
        appointment = str(UUID(receipt["id"]))
        session.request("GET", f"/api/v1/appointments/{appointment}", 200, private=True)
        if effects(appointment) != (1, 1, 2):
            raise RolloutError("smoke-failed")
        replay = session.request(
            "POST", "/api/v1/appointments", 200, key=key, private=True, json=payload
        ).json()
        if replay != receipt or effects(appointment) != (1, 1, 2):
            raise RolloutError("smoke-failed")
        path = f"/api/v1/appointments/{appointment}/cancel"
        cancelled = session.request("POST", path, 200, private=True, json={}).json()
        replay_cancel = session.request("POST", path, 200, private=True, json={}).json()
        if cancelled != replay_cancel or effects(appointment) != (1, 2, 4):
            raise RolloutError("smoke-failed")
        session.request("POST", "/api/v1/auth/logout", 204)
        session.request("GET", "/api/v1/appointments", 401)
        return sorted(SMOKE_CHECKS)
    except (ValueError, KeyError, TypeError, StopIteration):
        raise RolloutError("smoke-failed") from None
