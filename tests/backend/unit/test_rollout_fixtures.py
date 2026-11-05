import json

from tests.rollout.fixtures import BAD_API, Artifacts
from tools.deploy.release_manifest import Image


def test_local_manifests_are_explicit_immutable_protocol_fixtures(tmp_path):
    artifacts = Artifacts(tmp_path, "a" * 40)
    images = [
        Image(
            reference=f"127.0.0.1:{artifacts.port}/image@sha256:" + c * 64,
            image_id="sha256:" + c * 64,
            archive_sha256=c * 64,
            platform="linux/amd64",
        )
        for c in ("1", "2")
    ]
    first = artifacts.manifest(1, images[0], images[1], None)
    reference = artifacts.repository + "/releases@sha256:" + "f" * 64
    second = artifacts.manifest(2, images[0], images[1], reference)
    assert first.bootstrap and second.previous_stable == reference
    assert first.images["backend"].reference == second.images["backend"].reference
    assert second.services["api"] == second.services["worker"] == "backend"
    assert not any(
        word in json.dumps(second.model_dump()).lower() for word in ("password", "cookie")
    )


def test_known_bad_api_is_healthy_only_at_readiness_and_has_no_payload_logging():
    compile(BAD_API, "known-bad-api-fixture", "exec")
    assert "'/health/ready'" in BAD_API
    assert "503" in BAD_API
    assert "def log_message" in BAD_API
