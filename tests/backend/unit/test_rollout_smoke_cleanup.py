import pytest
from tests.backend.unit.smoke_fixtures import ACCOUNT, SmokeAPI
from tests.backend.unit.test_rollout_interruption import SimulatedCrash
from tools.deploy.rollout_smoke import business_smoke
from tools.deploy.rollout_smoke_journal import SmokeJournal, binding
from tools.deploy.rollout_state import SMOKE_CHECKS, RolloutError


def test_failed_candidates_cannot_leak_quota_when_cancellation_requires_the_restored_release(
    tmp_path,
):
    api = SmokeAPI()
    path = tmp_path / "private" / "smoke.json"
    for _ in range(5):
        api.fail_detail = api.fail_cancel = True
        with api.session() as session:
            with pytest.raises(RolloutError, match="smoke-failed"):
                business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))
        assert sum(row["status"] == "UPCOMING" for row in api.rows.values()) == 1
        assert SmokeJournal(path).load(binding("http://localhost:8080", ACCOUNT.email)) is not None
        api.fail_detail = api.fail_cancel = False
        with api.session() as session:
            assert business_smoke(
                session, ACCOUNT, api.effects, journal=SmokeJournal(path)
            ) == sorted(SMOKE_CHECKS)
        assert not any(row["status"] == "UPCOMING" for row in api.rows.values())
        assert not path.exists()
    assert len(api.rows) == len(api.keys) == 10


def test_crash_after_booking_commit_recovers_original_key_before_new_smoke(tmp_path):
    api = SmokeAPI()
    path = tmp_path / "private" / "smoke.json"
    api.crash_after_create = SimulatedCrash
    with api.session() as session:
        with pytest.raises(SimulatedCrash):
            business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))
    pending = SmokeJournal(path).load(binding("http://localhost:8080", ACCOUNT.email))
    assert pending is not None and pending.receipt is None
    assert len(api.keys) == len(api.rows) == 1
    api.crash_after_create = None
    with api.session() as session:
        business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))
    assert len(api.keys) == len(api.rows) == 2
    assert all(row["status"] == "CANCELLED" for row in api.rows.values())
    assert not path.exists()


def test_unresolved_cleanup_blocks_a_new_booking_and_keeps_the_private_intent(tmp_path):
    api = SmokeAPI()
    path = tmp_path / "private" / "smoke.json"
    api.fail_detail = api.fail_cancel = True
    with api.session() as session:
        with pytest.raises(RolloutError):
            business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))
    original = path.read_bytes()
    for _ in range(2):
        with api.session() as session:
            with pytest.raises(RolloutError, match="smoke-cleanup-failed"):
                business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))
        assert path.read_bytes() == original and len(api.rows) == 1
