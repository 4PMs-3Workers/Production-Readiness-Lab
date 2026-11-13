import json
import re
import shutil

from tests.rollout.fixtures import BAD_API, Artifacts
from tools.deploy.release_manifest import ROOT, Image, configuration


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


def test_manifest_uses_schema_revision_from_selected_source_tree(tmp_path):
    legacy_root = tmp_path / "legacy-source"
    current = configuration("local", False)
    for item in current.files:
        source = ROOT / item.path
        target = legacy_root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    lock = legacy_root / "tools/infra/images.lock.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "tools/infra/images.lock.json", lock)

    release_source = legacy_root / "tools/deploy/release_manifest.py"
    release_source.parent.mkdir(parents=True, exist_ok=True)
    release_source.write_text(
        re.sub(
            r'^SCHEMA = "[^"]+"$',
            'SCHEMA = "20261019_1200_proposal_replay"',
            (ROOT / "tools/deploy/release_manifest.py").read_text(encoding="utf-8"),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    artifacts = Artifacts(tmp_path / "artifacts", "a" * 40)
    images = [
        Image(
            reference=f"127.0.0.1:{artifacts.port}/image@sha256:" + character * 64,
            image_id="sha256:" + character * 64,
            archive_sha256=character * 64,
            platform="linux/amd64",
        )
        for character in ("1", "2")
    ]

    manifest = artifacts.manifest(1, images[0], images[1], None, root=legacy_root)

    assert manifest.accepted_schema_revisions == ["20261019_1200_proposal_replay"]


def test_known_bad_api_uses_real_booking_but_breaks_detail_and_cancellation():
    compile(BAD_API, "known-bad-api-fixture", "exec")
    assert "/health/ready" in BAD_API
    assert "503" in BAD_API
    assert "from app.main import app" in BAD_API
    assert "access_log=False" in BAD_API
    assert "path.endswith('/cancel')" in BAD_API
