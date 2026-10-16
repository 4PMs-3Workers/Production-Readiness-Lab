from hashlib import sha256
from uuid import UUID

import pytest

from app.domain.booking import (
    booking_request_hash,
    canonical_booking_request_bytes,
    require_description,
)
from app.schemas.appointments import AppointmentCreateRequest

PURPOSE_ID = UUID("AABBCCDD-0000-0000-0000-000000000001")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000002")


def request(*, starts_at: str, description: str | None = None) -> AppointmentCreateRequest:
    return AppointmentCreateRequest(
        purpose_id=PURPOSE_ID,
        staff_id=STAFF_ID,
        starts_at=starts_at,
        description=description,
    )


def test_timezone_equivalent_times_have_same_canonical_bytes_and_digest():
    first = request(starts_at="2026-10-16T09:00:00.123456+07:00", description="café")
    second = request(starts_at="2026-10-16T02:00:00.123456Z", description="café")

    assert canonical_booking_request_bytes(first) == canonical_booking_request_bytes(second)
    assert booking_request_hash(first) == booking_request_hash(second)


def test_canonical_payload_uses_only_business_fields_and_exact_normalized_values():
    value = request(starts_at="2026-10-16T09:00:00.123456+07:00", description=" café ")

    expected = (
        b'{"description":" caf\xc3\xa9 ","purpose_id":"aabbccdd-0000-0000-0000-000000000001",'
        b'"staff_id":"00000000-0000-0000-0000-000000000002",'
        b'"starts_at":"2026-10-16T02:00:00.123456Z"}'
    )
    assert canonical_booking_request_bytes(value) == expected
    assert booking_request_hash(value) == sha256(expected).digest()
    assert (
        booking_request_hash(value).hex()
        == "e4cba9444707f6fd06f49c98e4d0f50f370e4ca017c7d999b9716088dede73a5"
    )


def test_null_and_empty_description_have_distinct_hashes():
    absent = request(starts_at="2026-10-16T02:00:00Z")
    empty = request(starts_at="2026-10-16T02:00:00Z", description="")

    assert canonical_booking_request_bytes(absent) != canonical_booking_request_bytes(empty)
    assert booking_request_hash(absent) != booking_request_hash(empty)
    assert canonical_booking_request_bytes(absent) == (
        b'{"description":null,"purpose_id":"aabbccdd-0000-0000-0000-000000000001",'
        b'"staff_id":"00000000-0000-0000-0000-000000000002",'
        b'"starts_at":"2026-10-16T02:00:00.000000Z"}'
    )


def test_required_description_check_uses_blankness_without_mutating_value():
    text = "  brief context  "
    assert require_description(text, required=True) == text
    for text in (None, "", " \t\n "):
        with pytest.raises(ValueError, match="description is required"):
            require_description(text, required=True)
    assert require_description(None, required=False) is None


def test_hash_is_sha256_bytes_for_unicode_text_without_normalization():
    decomposed = "cafe\u0301"
    composed = "café"
    decomposed_request = request(starts_at="2026-10-16T02:00:00Z", description=decomposed)
    composed_request = request(starts_at="2026-10-16T02:00:00Z", description=composed)

    assert (
        booking_request_hash(decomposed_request)
        == sha256(canonical_booking_request_bytes(decomposed_request)).digest()
    )
    assert booking_request_hash(decomposed_request) != booking_request_hash(composed_request)
