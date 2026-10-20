"""Boundary validation for staff configuration, independent of MySQL or demo data."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.staff_availability import (
    StaffPurposeRequest,
    UnavailabilityCreateRequest,
    UnavailabilityQuery,
)


@pytest.mark.parametrize("value", [True, False])
def test_acceptance_requires_an_actual_boolean(value):
    assert StaffPurposeRequest(is_accepting=value).is_accepting is value


@pytest.mark.parametrize("value", [1, 0, "true", "false", None, [], {}])
def test_acceptance_rejects_coercion(value):
    with pytest.raises(ValidationError):
        StaffPurposeRequest(is_accepting=value)


def test_intervals_normalize_offsets_without_booking_restrictions():
    period = UnavailabilityCreateRequest.model_validate(
        {"starts_at": "2020-01-01T23:07:13+07:00", "ends_at": "2020-02-03T10:17:42+02:00"}
    )
    assert period.starts_at == datetime(2020, 1, 1, 16, 7, 13, tzinfo=UTC)
    assert period.ends_at == datetime(2020, 2, 3, 8, 17, 42, tzinfo=UTC)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-01-01T00:00:00", "2026-01-01T01:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00"),
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
        (False, "2026-01-01T01:00:00Z"),
        (1792454400, "2026-01-01T01:00:00Z"),
        ("1792454400", "2026-11-01T01:00:00Z"),
        ("0001-01-01T00:00:00+14:00", "2026-01-01T01:00:00Z"),
        ("0999-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
        ("2026-01-01T00:00:00Z", "9999-12-31T23:59:59-14:00"),
    ],
)
def test_invalid_interval_and_filter_values(start, end):
    with pytest.raises(ValidationError):
        UnavailabilityCreateRequest.model_validate({"starts_at": start, "ends_at": end})
    with pytest.raises(ValidationError):
        UnavailabilityQuery.model_validate({"from": start, "to": end})


@pytest.mark.parametrize("field", ["staff_id", "actor_id", "purpose_id", "request_id"])
def test_requests_reject_client_ownership_and_actor_fields(field):
    for model, body in [
        (StaffPurposeRequest, {"is_accepting": True}),
        (
            UnavailabilityCreateRequest,
            {"starts_at": "2026-01-01T00:00:00Z", "ends_at": "2026-01-02T00:00:00Z"},
        ),
        (UnavailabilityQuery, {}),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate({**body, field: "forged"})


@pytest.mark.parametrize(
    "values", [{}, {"from": "2026-01-01T01:00:00+07:00"}, {"to": "2026-01-01T00:00:00Z"}]
)
def test_filter_bounds_are_optional(values):
    assert UnavailabilityQuery.model_validate(values).page_size == 20
