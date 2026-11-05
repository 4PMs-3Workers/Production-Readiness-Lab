import httpx
import pytest
from tests.backend.unit.smoke_fixtures import ACCOUNT, SmokeAPI
from tests.backend.unit.test_rollout_runtime import runtime
from tools.deploy.rollout_http import SmokeSession
from tools.deploy.rollout_runtime import Deadline
from tools.deploy.rollout_smoke import business_smoke
from tools.deploy.rollout_smoke_journal import SmokeJournal
from tools.deploy.rollout_state import RolloutError


@pytest.mark.parametrize(
    "fault", ["receipt-status", "receipt-id", "detail-status", "detail-time", "db"]
)
def test_equal_responses_and_counts_cannot_substitute_for_persisted_cancellation(tmp_path, fault):
    api = SmokeAPI()

    def handler(request):
        response = api.handle(request)
        path = request.url.path
        if path.endswith("/cancel"):
            body = response.json()
            if fault == "receipt-status":
                body["status"] = "UPCOMING"
            if fault == "receipt-id":
                body["id"] = "44444444-4444-4444-4444-444444444444"
            return httpx.Response(200, json=body, headers=response.headers)
        if path.startswith("/api/v1/appointments/") and request.method == "GET":
            body = response.json()
            if body["status"] == "CANCELLED":
                if fault == "detail-status":
                    body["status"] = "UPCOMING"
                if fault == "detail-time":
                    body["cancelled_at"] = "2026-11-05T00:02:00Z"
            return httpx.Response(200, json=body, headers=response.headers)
        return response

    journal = SmokeJournal(tmp_path / "private" / "smoke.json")
    with SmokeSession(
        "http://localhost:8080", Deadline(30), transport=httpx.MockTransport(handler)
    ) as session:
        with pytest.raises(RolloutError, match="smoke-failed"):
            business_smoke(
                session,
                ACCOUNT,
                api.effects,
                journal=journal,
                persisted_status=lambda identifier: "UPCOMING" if fault == "db" else "CANCELLED",
            )
    assert api.rows and all(api.effects(identifier) == (1, 2, 4) for identifier in api.rows)
    assert journal.path is not None and journal.path.exists()


def test_read_only_status_probe_binds_only_a_validated_uuid(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    statements = []

    def sql(statement):
        statements.append(statement)
        return b"CANCELLED"

    monkeypatch.setattr(host, "sql", sql)
    assert host.appointment_status("33333333-3333-3333-3333-333333333333") == "CANCELLED"
    assert statements == [
        "SELECT status FROM prl.appointments WHERE id=UNHEX('33333333333333333333333333333333');"
    ]
    with pytest.raises(ValueError):
        host.appointment_status("untrusted SQL input")
    assert len(statements) == 1
