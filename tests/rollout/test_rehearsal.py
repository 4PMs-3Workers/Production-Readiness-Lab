import os

from tools.deploy.release_manifest import ROOT
from tools.deploy.rollout_rehearsal import rehearse


def test_known_bad_release_is_replaced_by_stable_with_committed_data_preserved():
    assert os.environ.get("RUN_ROLLOUT_REHEARSAL") == "1", "Required rollback rehearsal cannot skip"
    report = rehearse(ROOT / "build/rollback-review")
    assert report["outcome"] == "passed"
    assert report["proof_kind"] == "automated-linux-compose-lab"
    assert report["rollback_seconds"] <= 300
    assert report["committed_before"] == report["committed_during"] == 1
    assert report["committed_records_preserved"] and report["replay_and_effects_verified"]
    assert report["restored_image_ids"]["backend"] != report["bad_backend_image_id"]
