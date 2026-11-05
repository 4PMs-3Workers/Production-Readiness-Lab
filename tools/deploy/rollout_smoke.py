"""Gateway business verification with private receipts and exact replay effects."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_smoke_journal import SmokeJournal, binding
from tools.deploy.rollout_state import SMOKE_CHECKS, RolloutError

from app.schemas.appointments import AppointmentCancelResponse

Effects = Callable[[str], tuple[int, int, int]]
PersistedStatus = Callable[[str], str]


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


def cancel_and_verify(
    session: SmokeSession,
    appointment: str,
    effects: Effects,
    persisted_status: PersistedStatus | None = None,
) -> None:
    path = f"/api/v1/appointments/{appointment}/cancel"
    response = session.request("POST", path, 200, private=True, json={})
    receipt = AppointmentCancelResponse.model_validate_json(response.content, strict=True)
    cancelled = response.json()
    if str(receipt.id) != appointment:
        raise RolloutError("smoke-failed")
    replay = session.request("POST", path, 200, private=True, json={}).json()
    detail = session.request("GET", f"/api/v1/appointments/{appointment}", 200, private=True).json()
    if (
        replay != cancelled
        or detail.get("id") != appointment
        or detail.get("status") != "CANCELLED"
        or datetime.fromisoformat(detail["cancelled_at"].replace("Z", "+00:00"))
        != receipt.cancelled_at
        or effects(appointment) != (1, 2, 4)
        or persisted_status is not None
        and persisted_status(appointment) != "CANCELLED"
    ):
        raise RolloutError("smoke-failed")


def cleanup_pending(
    session: SmokeSession,
    account: SmokeCredentials,
    journal: SmokeJournal,
    effects: Effects,
    persisted_status: PersistedStatus | None = None,
) -> None:
    account_id = binding(session.origin, account.email)
    pending = journal.load(account_id)
    if pending is None:
        return
    try:
        # Reconcile even a known receipt before cancelling: never trust a broken
        # candidate's appointment ID without the original key/payload replay.
        receipt = session.request(
            "POST",
            "/api/v1/appointments",
            (200, 201) if pending.receipt is None else 200,
            key=pending.key,
            private=True,
            json=pending.payload.model_dump(mode="json"),
        ).json()
        pending = journal.received(account_id, receipt)
        assert pending.receipt is not None
        appointment = str(pending.receipt.id)
        cancel_and_verify(session, appointment, effects, persisted_status)
        journal.cleared(account_id, appointment)
    except Exception:
        # Keep the private intent until a healthy restored release verifies it.
        raise RolloutError("smoke-cleanup-failed") from None


def business_smoke(
    session: SmokeSession,
    account: SmokeCredentials,
    effects: Effects,
    *,
    journal: SmokeJournal | None = None,
    persisted_status: PersistedStatus | None = None,
) -> list[str]:
    journal = journal if journal is not None else SmokeJournal()
    account_id = binding(session.origin, account.email)
    try:
        session.request("GET", "/login", 200)
        session.login(account)
        cleanup_pending(session, account, journal, effects, persisted_status)
        session.request("GET", "/api/v1/appointments?page=1&page_size=50", 200, private=True)
        payload, key = booking_payload(session, account), uuid4().hex
        journal.begin(account_id, key, payload)
        receipt = session.request(
            "POST", "/api/v1/appointments", 201, key=key, private=True, json=payload
        ).json()
        journal.received(account_id, receipt)
        appointment = str(UUID(receipt["id"]))
        session.request("GET", f"/api/v1/appointments/{appointment}", 200, private=True)
        if effects(appointment) != (1, 1, 2):
            raise RolloutError("smoke-failed")
        replay = session.request(
            "POST", "/api/v1/appointments", 200, key=key, private=True, json=payload
        ).json()
        if replay != receipt or effects(appointment) != (1, 1, 2):
            raise RolloutError("smoke-failed")
        cancel_and_verify(session, appointment, effects, persisted_status)
        journal.cleared(account_id, appointment)
        session.request("POST", "/api/v1/auth/logout", 204)
        session.request("GET", "/api/v1/appointments", 401)
        return sorted(SMOKE_CHECKS)
    except Exception as error:
        if session.csrf:
            try:
                cleanup_pending(session, account, journal, effects, persisted_status)
            except Exception:
                pass
        if isinstance(error, RolloutError):
            raise
        raise RolloutError("smoke-failed") from None
