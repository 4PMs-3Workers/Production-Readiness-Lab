import os

import pytest
from tools.deploy.rollout_smoke_journal import SmokeJournal, binding
from tools.deploy.rollout_state import RolloutError

ACCOUNT = binding("https://lab.example.invalid", "student@example.invalid")
PAYLOAD = dict(
    purpose_id="11111111-1111-1111-1111-111111111111",
    staff_id="22222222-2222-2222-2222-222222222222",
    starts_at="2026-11-06T09:00:00Z",
)
RECEIPT = dict(
    id="33333333-3333-3333-3333-333333333333",
    status="UPCOMING",
    created_at="2026-11-05T00:00:00Z",
)


def test_private_intent_survives_restarts_without_credentials_or_free_text(tmp_path):
    path = tmp_path / "private" / "smoke.json"
    SmokeJournal(path).begin(ACCOUNT, "a" * 32, PAYLOAD)
    recovered = SmokeJournal(path).load(ACCOUNT)
    assert recovered is not None and recovered.receipt is None
    assert recovered.payload.model_dump(mode="json") == PAYLOAD
    assert recovered.key == "a" * 32
    SmokeJournal(path).received(ACCOUNT, RECEIPT)
    received = SmokeJournal(path).load(ACCOUNT)
    assert received is not None and received.receipt is not None
    assert str(received.receipt.id) == RECEIPT["id"]
    assert "33333333" not in repr(received) and "a" * 32 not in repr(received)
    assert "student@example.invalid" not in path.read_text()
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    SmokeJournal(path).cleared(ACCOUNT, RECEIPT["id"])
    assert SmokeJournal(path).load(ACCOUNT) is None


def test_unresolved_transaction_cannot_be_overwritten_or_claimed_by_another_account(tmp_path):
    journal = SmokeJournal(tmp_path / "private" / "smoke.json")
    original = journal.begin(ACCOUNT, "a" * 32, PAYLOAD)
    with pytest.raises(RolloutError, match="smoke-cleanup-failed"):
        journal.begin(ACCOUNT, "b" * 32, PAYLOAD)
    with pytest.raises(RolloutError, match="smoke-cleanup-failed"):
        journal.load("b" * 64)
    assert journal.load(ACCOUNT) == original


@pytest.mark.parametrize(
    "extra", [dict(description="private consultation"), dict(password="private credential")]
)
def test_private_intent_rejects_fields_outside_non_free_text_booking_contract(tmp_path, extra):
    path = tmp_path / "private" / "smoke.json"
    with pytest.raises(RolloutError, match="invalid-state"):
        SmokeJournal(path).begin(ACCOUNT, "a" * 32, dict(PAYLOAD, **extra))
    assert not path.exists()


def test_failed_private_state_write_prevents_a_new_booking_intent(tmp_path, monkeypatch):
    journal = SmokeJournal(tmp_path / "private" / "smoke.json")

    def fail(*args):
        raise OSError("private disk detail")

    assert journal._files is not None
    monkeypatch.setattr(journal._files, "_write", fail)
    with pytest.raises(RolloutError, match="invalid-state"):
        journal.begin(ACCOUNT, "a" * 32, PAYLOAD)
    assert journal.load(ACCOUNT) is None


def test_private_receipt_cannot_be_rebound_or_cleared_without_matching_identity(tmp_path):
    journal = SmokeJournal(tmp_path / "private" / "smoke.json")
    journal.begin(ACCOUNT, "a" * 32, PAYLOAD)
    with pytest.raises(RolloutError, match="invalid-state"):
        journal.cleared(ACCOUNT, RECEIPT["id"])
    original = journal.received(ACCOUNT, RECEIPT)
    with pytest.raises(RolloutError, match="smoke-cleanup-failed"):
        journal.received(ACCOUNT, dict(RECEIPT, id="44444444-4444-4444-4444-444444444444"))
    assert journal.load(ACCOUNT) == original
