from datetime import date
from importlib import import_module, util

import pytest
from pydantic import ValidationError

from app.schemas.appointments import AppointmentCancelRequest


def _schemas():
    assert util.find_spec("app.schemas.staff_appointments") is not None, "staff schemas are missing"
    return import_module("app.schemas.staff_appointments")


def test_staff_list_query_defaults_and_pagination_bounds():
    StaffAppointmentListQuery = _schemas().StaffAppointmentListQuery
    query = StaffAppointmentListQuery()
    assert query.model_dump() == {"page": 1, "page_size": 20, "status": None, "date": None}
    assert StaffAppointmentListQuery(page_size=100).page_size == 100
    for values in ({"page": 0}, {"page_size": 101}, {"actor_id": "anything"}):
        with pytest.raises(ValidationError):
            StaffAppointmentListQuery.model_validate(values)


def test_staff_list_query_accepts_status_and_business_date():
    StaffAppointmentListQuery = _schemas().StaffAppointmentListQuery
    query = StaffAppointmentListQuery.model_validate({"status": "UPCOMING", "date": "2026-10-20"})
    assert query.status == "UPCOMING"
    assert query.date == date(2026, 10, 20)
    with pytest.raises(ValidationError):
        StaffAppointmentListQuery.model_validate({"status": "UNKNOWN"})
    with pytest.raises(ValidationError):
        StaffAppointmentListQuery.model_validate({"date": "20-10-2026"})


@pytest.mark.parametrize(
    "status", ["UPCOMING", "CHANGE_PENDING", "CANCELLED", "COMPLETED", "NO_SHOW"]
)
def test_staff_list_query_accepts_every_appointment_status(status):
    StaffAppointmentListQuery = _schemas().StaffAppointmentListQuery
    assert StaffAppointmentListQuery(status=status).status == status


def test_staff_cancel_reuses_the_bounded_optional_reason_contract():
    assert AppointmentCancelRequest().reason is None
    assert AppointmentCancelRequest(reason="x" * 2000).reason == "x" * 2000
    with pytest.raises(ValidationError):
        AppointmentCancelRequest(reason="x" * 2001)


@pytest.mark.parametrize("status", ["COMPLETED", "NO_SHOW"])
def test_staff_outcome_accepts_only_the_two_terminal_outcomes(status):
    StaffAppointmentOutcomeRequest = _schemas().StaffAppointmentOutcomeRequest
    assert StaffAppointmentOutcomeRequest.model_validate({"status": status}).status == status


@pytest.mark.parametrize(
    "payload", [{"status": "UPCOMING"}, {"status": "NO_SHOW", "actor_id": "x"}]
)
def test_staff_outcome_rejects_unsupported_status_and_client_actor(payload):
    StaffAppointmentOutcomeRequest = _schemas().StaffAppointmentOutcomeRequest
    with pytest.raises(ValidationError):
        StaffAppointmentOutcomeRequest.model_validate(payload)
