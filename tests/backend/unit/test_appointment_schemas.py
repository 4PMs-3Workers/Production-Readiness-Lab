from datetime import datetime
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.appointments import (
    AppointmentCreateRequest,
    AppointmentCreateResponse,
    IdempotencyKey,
)

PURPOSE_ID = UUID("AABBCCDD-0000-0000-0000-000000000001")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_create_request_requires_timezone_aware_timestamp():
    with pytest.raises(ValidationError):
        AppointmentCreateRequest.model_validate(
            {
                "purpose_id": PURPOSE_ID,
                "staff_id": STAFF_ID,
                "starts_at": "2026-10-16T09:00:00",
            }
        )


def test_create_request_rejects_client_controlled_fields():
    with pytest.raises(ValidationError):
        AppointmentCreateRequest.model_validate(
            {
                "purpose_id": PURPOSE_ID,
                "staff_id": STAFF_ID,
                "starts_at": "2026-10-16T09:00:00Z",
                "student_id": "00000000-0000-0000-0000-000000000003",
            }
        )


def test_description_length_is_bounded_and_content_is_preserved():
    description = "  Unicode: café 🧑‍⚕️  "
    request = AppointmentCreateRequest.model_validate(
        {
            "purpose_id": PURPOSE_ID,
            "staff_id": STAFF_ID,
            "starts_at": "2026-10-16T09:00:00Z",
            "description": description,
        }
    )
    assert request.description == description

    with pytest.raises(ValidationError):
        AppointmentCreateRequest.model_validate(
            {
                "purpose_id": PURPOSE_ID,
                "staff_id": STAFF_ID,
                "starts_at": "2026-10-16T09:00:00Z",
                "description": "x" * 2001,
            }
        )


def test_optional_description_can_be_null_or_empty():
    common = {
        "purpose_id": PURPOSE_ID,
        "staff_id": STAFF_ID,
        "starts_at": "2026-10-16T09:00:00Z",
    }
    assert AppointmentCreateRequest.model_validate(common).description is None
    assert AppointmentCreateRequest.model_validate(common | {"description": ""}).description == ""


def test_idempotency_key_requires_nonblank_value_within_storage_bounds():
    # Pydantic accepts Annotated aliases at runtime; Pyright cannot express them as TypeForm.
    adapter = TypeAdapter[str](cast(Any, IdempotencyKey))
    assert adapter.validate_python(" x ") == " x "
    for key in ("", " \t\r\n", "x" * 129):
        with pytest.raises(ValidationError):
            adapter.validate_python(key)
    assert adapter.validate_python("x" * 128) == "x" * 128


def test_create_response_contains_immutable_creation_receipt_fields():
    response = AppointmentCreateResponse(
        id=UUID("00000000-0000-0000-0000-000000000004"),
        status="UPCOMING",
        created_at=datetime.fromisoformat("2026-10-16T02:00:00+00:00"),
    )
    assert response.status == "UPCOMING"
    assert set(AppointmentCreateResponse.model_fields) == {"id", "status", "created_at"}


@pytest.mark.parametrize(
    "starts_at",
    [
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
    ],
)
def test_create_request_rejects_timestamps_outside_utc_range(starts_at: str):
    with pytest.raises(ValidationError):
        AppointmentCreateRequest.model_validate(
            {"purpose_id": PURPOSE_ID, "staff_id": STAFF_ID, "starts_at": starts_at}
        )
