import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tests.backend.unit.smoke_fixtures import ACCOUNT, SmokeAPI
from tests.backend.unit.test_rollout_recovery_retry import reload_controller
from tools.deploy.release_manifest import canonical
from tools.deploy.rollout_smoke import business_smoke
from tools.deploy.rollout_smoke_journal import SmokeJournal


@pytest.mark.parametrize("failed_cleanup", [False, True])
def test_automatic_or_retried_rollback_cleans_candidate_booking_before_fresh_smoke(
    tmp_path, failed_cleanup
):
    host, api = controller(tmp_path), SmokeAPI()
    path = host.store.directory / "private" / "smoke-transaction.json"
    block_restore = [False]

    def smoke(runtime):
        assert runtime.manifest is not None
        bad = runtime.manifest.release_id != "release-123-1"
        api.fail_detail = bad
        api.fail_cancel = bad or block_restore[0]
        # Reopen the disk journal for every operation, including retry on a new controller.
        with api.session() as session:
            return business_smoke(session, ACCOUNT, api.effects, journal=SmokeJournal(path))

    host.control.smoke = smoke
    first = host.deploy(reference())
    assert first.operation is not None and first.operation.phase == "succeeded"
    host.runtime.previous_stable = first.stable
    preserved = {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
    for identifier in preserved:
        api.rows[identifier] = dict(id=identifier, status="UPCOMING")

    for number in range(2, 7):
        host.registry.releases[reference(number)] = release(122 + number, reference())
        block_restore[0] = failed_cleanup
        outcome = host.deploy(reference(number))
        assert outcome.stable == first.stable
        assert outcome.operation is not None
        if failed_cleanup:
            assert outcome.operation.phase == "failed"
            assert outcome.operation.events[-1].reason == "smoke-cleanup-failed"
            assert path.exists()
            block_restore[0] = False
            reload_controller(host)
            outcome = host.rollback()
        assert outcome.operation is not None and outcome.operation.phase == "rolled_back"
        assert outcome.stable == first.stable
        assert not path.exists()
        assert {key for key, row in api.rows.items() if row["status"] == "UPCOMING"} == preserved
        evidence = canonical(outcome).decode()
        assert all(key not in evidence for key in api.keys)
        assert all(identifier not in evidence for identifier in api.rows)
