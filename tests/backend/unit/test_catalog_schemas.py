from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.availability import (
    AvailabilityQuery,
    AvailabilityResponse,
    AvailableSlot,
)
from app.schemas.catalog import PurposeItem, PurposeQuery, StaffItem, StaffQuery
from app.schemas.common import PageResponse, UtcTimestamp


def test_pagination_defaults_and_public_fields():
    assert PurposeQuery().model_dump() == {"page": 1, "page_size": 20}
    assert set(StaffItem.model_fields) == {"id", "name"}
    assert set(PurposeItem.model_fields) == {
        "id",
        "code",
        "name",
        "duration_minutes",
        "requires_free_text",
    }
    assert set(AvailabilityResponse.model_fields) == {"date", "timezone", "slots"}


@pytest.mark.parametrize(
    "model,values",
    [
        (PurposeQuery, {"page": 0}),
        (PurposeQuery, {"page_size": 0}),
        (PurposeQuery, {"page_size": 101}),
        (StaffQuery, {}),
        (StaffQuery, {"purpose_id": "not-a-uuid"}),
        (StaffQuery, {"purpose_id": str(UUID(int=1)), "student_id": str(UUID(int=2))}),
        (AvailabilityQuery, {"purpose_id": str(UUID(int=1)), "staff_id": str(UUID(int=2)), "date": "20261016"}),
        (AvailabilityQuery, {"purpose_id": str(UUID(int=1)), "staff_id": str(UUID(int=2)), "date": "2026-10-16T00:00:00Z"}),
        (AvailabilityQuery, {"purpose_id": str(UUID(int=1)), "staff_id": str(UUID(int=2)), "date": "2026-02-30"}),
        (AvailabilityQuery, {"purpose_id": str(UUID(int=1)), "staff_id": str(UUID(int=2)), "date": "２０２６-１０-１６"}),
        (AvailabilityQuery, {"purpose_id": str(UUID(int=1)), "staff_id": str(UUID(int=2)), "date": "2026-10-16", "student_id": "x"}),
    ],
)
def test_invalid_query_values_are_rejected(model, values):
    with pytest.raises(ValidationError):
        model(**values)


@pytest.mark.parametrize("page_size", [1, 100])
def test_purpose_query_accepts_page_size_bounds(page_size):
    assert PurposeQuery(page_size=page_size).page_size == page_size


def test_queries_accept_non_v4_uuids_and_exact_iso_date():
    query = AvailabilityQuery(
        purpose_id="00000000-0000-0000-0000-000000000001",
        staff_id="00000000-0000-0000-0000-000000000002",
        date="2026-10-16",
    )
    assert query.date.isoformat() == "2026-10-16"


def test_page_response_preserves_metadata():
    response = PageResponse[StaffItem](items=[], page=50, page_size=20, total=2)
    assert response.model_dump() == {"items": [], "page": 50, "page_size": 20, "total": 2}


def test_available_slot_serializes_aware_time_as_utc_with_microseconds():
    slot = AvailableSlot(
        starts_at=UtcTimestamp(datetime.fromisoformat("2026-10-16T09:00:00.123456+07:00")),
        ends_at=UtcTimestamp(datetime.fromisoformat("2026-10-16T09:30:00.123456+07:00")),
    )
    assert slot.model_dump(mode="json") == {
        "starts_at": "2026-10-16T02:00:00.123456Z",
        "ends_at": "2026-10-16T02:30:00.123456Z",
    }


def test_shared_utc_timestamp_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        UtcTimestamp(datetime(2026, 10, 16, 9, 0))
