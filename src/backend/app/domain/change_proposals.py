"""Stable canonicalization for one staff proposal creation intent."""

import json
from datetime import UTC
from hashlib import sha256
from uuid import UUID

from app.schemas.change_proposals import ProposalCreateRequest


def proposal_request_hash(
    payload: ProposalCreateRequest, *, appointment_id: UUID, actor_id: UUID
) -> bytes:
    canonical = {
        "operation": "appointment-change-proposal-create:v1",
        "appointment_id": str(appointment_id),
        "actor_id": str(actor_id),
        "expected_proposal_version": payload.expected_proposal_version,
        "expected_pending_proposal_id": (
            str(payload.expected_pending_proposal_id)
            if payload.expected_pending_proposal_id is not None
            else None
        ),
        "proposed_starts_at": payload.proposed_starts_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "proposed_location": payload.proposed_location,
    }
    return sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).digest()
