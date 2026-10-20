"""Proposal wire validation and durable intent identity are independent of database time."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.change_proposals import proposal_request_hash
from app.factory import create_app
from app.schemas.change_proposals import ProposalCreateRequest, ProposalDecisionRequest

BODY = {
    "expected_proposal_version": 0,
    "expected_pending_proposal_id": None,
    "proposed_starts_at": "2026-10-23T09:00:00+07:00",
    "proposed_location": None,
}


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_id": str(uuid4())},
        {"status": "PENDING"},
        {"duration_minutes": 45},
        {"proposed_ends_at": "2026-10-23T10:00:00+07:00"},
        {"expires_at": "2026-10-24T00:00:00Z"},
        {"expected_proposal_version": True},
        {"expected_proposal_version": "0"},
        {"expected_proposal_version": 0.0},
        {"expected_proposal_version": -1},
        {"expected_proposal_version": 9007199254740992},
        {"proposed_location": "  "},
        {"proposed_location": 12},
        {"proposed_location": "x" * 501},
        {"proposed_starts_at": 1792720800},
        {"proposed_starts_at": "2026-10-23T09:00:00"},
        {"proposed_starts_at": "2026-10-23T02:00:00.1234567Z"},
    ],
)
def test_strict_proposal_wire_contract(changes):
    with pytest.raises(ValidationError):
        ProposalCreateRequest.model_validate(BODY | changes)


@pytest.mark.parametrize("missing", list(BODY))
def test_nullable_fields_are_still_required(missing):
    with pytest.raises(ValidationError):
        ProposalCreateRequest.model_validate(
            {key: value for key, value in BODY.items() if key != missing}
        )


def test_equivalent_offsets_replay_but_changed_context_location_actor_or_path_do_not():
    appointment_id, actor_id = uuid4(), uuid4()
    original = ProposalCreateRequest.model_validate(BODY)

    def digest(payload=original, appointment=appointment_id, actor=actor_id):
        return proposal_request_hash(payload, appointment_id=appointment, actor_id=actor)

    same = ProposalCreateRequest.model_validate(
        BODY | {"proposed_starts_at": "2026-10-23T02:00:00Z"}
    )
    assert digest() == digest(same)
    for changes in (
        {"expected_proposal_version": 1},
        {"expected_pending_proposal_id": uuid4()},
        {"proposed_location": "Room A"},
        {"proposed_starts_at": "2026-10-23T03:00:00Z"},
    ):
        assert digest(ProposalCreateRequest.model_validate(BODY | changes)) != digest()
    assert digest(appointment=uuid4()) != digest()
    assert digest(actor=uuid4()) != digest()
    with pytest.raises(ValidationError):
        ProposalDecisionRequest.model_validate({"status": "ACCEPTED"})


def test_openapi_declares_proposal_context_receipts_and_security_without_private_metadata(
    auth_settings,
):
    document = create_app(auth_settings).openapi()
    base = "/api/v1/staff/me/appointments/{appointment_id}/change-proposals"
    create = document["paths"][base]["post"]
    parameters = {item["name"]: item for item in create["parameters"]}
    assert parameters["Idempotency-Key"]["required"]
    assert parameters["X-CSRF-Token"]["required"]
    assert create["security"] == [{"prl_session": []}]
    assert {"200", "201", "409", "503"} <= create["responses"].keys()
    assert "X-CSRF-Token" not in str(document["paths"][base]["get"].get("parameters"))
    schemas = document["components"]["schemas"]
    assert "status" not in schemas["ProposalCreateReceipt"]["properties"]
    assert "creation_request_key" not in str(schemas)
    assert "creation_request_hash" not in str(schemas)
