"""Independent CSV oracle and deterministic timeout injection for VT-071."""

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from tests.backend.integration.test_appointments_api import (
    ORIGIN,
    assert_private,
    install_overrides,
    student_context,
)

from app.factory import create_app
from app.repositories import appointments
from app.schemas.appointments import AppointmentListItem
from app.schemas.common import UtcTimestamp


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=1+1", "'=1+1"),
        (" +SUM(A1)", "' +SUM(A1)"),
        ("-1", "'-1"),
        ("@X", "'@X"),
        ("\t=1", "'\t=1"),
        ("\r\n@X", "'\r\n@X"),
        ("\tplain", "'\tplain"),
        ("\rplain", "'\rplain"),
        ("\nplain", "'\nplain"),
        ("＝1+1", "'＝1+1"),
        ("＋1", "'＋1"),
        ("－1", "'－1"),
        ("＠X", "'＠X"),
        ("Tư vấn, học tập", "Tư vấn, học tập"),
        ('a"b\nc', 'a"b\nc'),
        ("", ""),
        (None, ""),
    ],
)
def test_csv_text_and_formula_contract(value, expected):
    # Imported only in test function; the API red phase ran before this implementation existed.
    from app.services.appointment_export import render_csv

    instant = datetime(2026, 10, 16, 1, tzinfo=UTC)
    item = AppointmentListItem(
        id=UUID(int=1),
        purpose_id=UUID(int=2),
        purpose_code="FIXED",
        purpose_name="Tư vấn",
        staff_id=UUID(int=3),
        staff_name="Cố vấn",
        starts_at=UtcTimestamp(instant),
        ends_at=UtcTimestamp(instant + timedelta(minutes=30)),
        duration_minutes=30,
        status="UPCOMING",
        created_at=UtcTimestamp(instant),
        location=value,
    )
    parsed = list(csv.DictReader(io.StringIO(render_csv([item]))))
    assert parsed[0]["location"] == expected
    assert parsed[0]["starts_at"] == "2026-10-16T01:00:00Z"
    assert set(parsed[0]) == {
        "id",
        "purpose_code",
        "purpose_name",
        "staff_name",
        "starts_at",
        "ends_at",
        "duration_minutes",
        "status",
        "location",
    }


async def test_export_timeout_is_sanitized(auth_settings, monkeypatch):
    app = create_app(auth_settings)
    install_overrides(app, student_context())

    async def timeout(*args, **kwargs):
        raise TimeoutError("PRIVATE DATABASE INFORMATION")

    monkeypatch.setattr(appointments, "read_student_appointment_export", timeout)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        response = await client.get("/api/v1/appointments/export")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "PRIVATE" not in response.text
    assert "content-disposition" not in response.headers
    assert_private(response, error_class="service")


@pytest.mark.parametrize("timestamp", ["2026-10-16T08:00:00+07:00", "2026-10-15T20:00:00-05:00"])
def test_export_timestamp_does_not_depend_on_local_clock(timestamp):
    """TC17: equivalent instants produce UTC text without relying on machine timezone."""
    from app.services.appointment_export import render_csv

    instant = datetime.fromisoformat(timestamp)
    item = AppointmentListItem(
        id=UUID(int=1),
        purpose_id=UUID(int=2),
        purpose_code="FIXED",
        purpose_name="Tư vấn",
        staff_id=UUID(int=3),
        staff_name="Cố vấn",
        starts_at=UtcTimestamp(instant),
        ends_at=UtcTimestamp(instant + timedelta(minutes=30)),
        duration_minutes=30,
        status="UPCOMING",
        created_at=UtcTimestamp(instant),
        location=None,
    )
    parsed = list(csv.DictReader(io.StringIO(render_csv([item]))))
    assert parsed[0]["starts_at"] == "2026-10-16T01:00:00Z"
    assert parsed[0]["ends_at"] == "2026-10-16T01:30:00Z"
