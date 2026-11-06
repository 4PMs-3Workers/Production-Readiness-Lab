from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast

import pytest
from tools.backup import rehearsal

ID = "12345678-1234-1234-1234-123456789012"


@pytest.mark.parametrize(
    "fault", [None, "receipt-status", "receipt-id", "detail-status", "persisted-status"]
)
def test_recovered_smoke_requires_actual_persisted_cancellation(monkeypatch, fault):
    cancelled = []
    receipt = dict(id=ID, status="CANCELLED", cancelled_at="2026-11-01T00:00:00Z")
    if fault == "receipt-status":
        receipt["status"] = "UPCOMING"
    elif fault == "receipt-id":
        receipt["id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    def request(client, method, path, expected, **kwargs):
        if path == "/api/v1/appointments?page=1&page_size=50":
            value = dict(items=[dict(id="persisted")])
        elif path.endswith("/cancel"):
            cancelled.append(True)
            value = receipt
        elif path == "/api/v1/appointments" and method == "POST":
            value = dict(id=ID)
        elif path == "/api/v1/appointments/" + ID:
            value = dict(
                id=ID,
                status="UPCOMING" if fault == "detail-status" else "CANCELLED",
                cancelled_at=receipt["cancelled_at"],
            )
        else:
            value = {}
        return SimpleNamespace(json=lambda: value)

    monkeypatch.setattr(rehearsal, "request", request)
    monkeypatch.setattr(rehearsal, "booking_payload", lambda client: dict())
    stack = SimpleNamespace(
        api_client=lambda: nullcontext(None),
        login=lambda *args: "csrf",
        origin="http://localhost",
        student_email="fictional",
        student_password="private-not-logged",
        effects=lambda appointment: (1, 2, 4) if cancelled else (1, 1, 2),
        appointment_status=lambda appointment: (
            "UPCOMING" if fault == "persisted-status" else "CANCELLED"
        ),
    )
    actor = cast(rehearsal.RecoveryStack, stack)
    if fault is None:
        assert all(rehearsal.application_smoke(actor, "persisted", "later").values())
    else:
        with pytest.raises((ValueError, rehearsal.AcceptanceFailure)):
            rehearsal.application_smoke(actor, "persisted", "later")
