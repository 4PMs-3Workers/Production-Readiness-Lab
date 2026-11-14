"""Pure booking request canonicalization and content validation helpers."""

import json
from datetime import UTC
from hashlib import sha256

from app.schemas.appointments import AppointmentCreateRequest


def require_description(description: str | None, *, required: bool) -> str | None:
    """Require nonblank content when needed, returning the submitted text unchanged."""
    if required and (description is None or not description.strip()):
        raise ValueError("description is required")
    return description


def booking_quota_reached(active_count: int, limit: int) -> bool:
    """Return whether the number of quota-consuming appointments reaches the policy limit."""
    if type(active_count) is not int or active_count < 0:
        raise ValueError("Active appointment count must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 32767:
        raise ValueError("Booking quota must be a positive SMALLINT")
    return active_count >= limit


def canonical_booking_request_bytes(request: AppointmentCreateRequest) -> bytes:
    """Serialize documented business fields for stable idempotency comparison."""
    payload = {
        "description": request.description,
        "purpose_id": str(request.purpose_id).lower(),
        "staff_id": str(request.staff_id).lower(),
        "starts_at": request.starts_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def booking_request_hash(request: AppointmentCreateRequest) -> bytes:
    """Return the raw 32-byte SHA-256 digest used for an appointment creation request."""
    return sha256(canonical_booking_request_bytes(request)).digest()
