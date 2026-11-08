"""Upgrade a running predecessor with its retained manifest and MySQL volume."""

import os
import tarfile

import pytest
from tests.rollout.fixtures import Artifacts, RolloutStack
from tools.deploy import demo
from tools.deploy.release_images import command
from tools.deploy.release_manifest import ROOT
from tools.deploy.rollout import reviewed_transition
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_rehearsal import prerequisites
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime
from tools.deploy.rollout_smoke import booking_payload, business_smoke
from tools.deploy.rollout_smoke_journal import SmokeJournal
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore

BASE = "724169a33b852a6cc6b255aff7cb92fffed89908"


def test_reviewed_configuration_upgrade_and_rollback_preserve_the_existing_host(tmp_path):
    assert os.environ.get("RUN_ROLLOUT_REHEARSAL") == "1", "Required rollout rehearsal cannot skip"
    source = prerequisites()
    archive = tmp_path / "base.tar"
    base = tmp_path / "base"
    base.mkdir()
    command(["git", "archive", "--format=tar", "-o", str(archive), BASE], cwd=ROOT)
    with tarfile.open(archive) as tree:
        tree.extractall(base, filter="data")

    artifacts = Artifacts(tmp_path / "artifacts", source)
    stack = None
    try:
        artifacts.start()
        old_backend = artifacts.build(
            "base-backend", base / "Dockerfile.backend", base, source_sha=BASE
        )
        old_frontend = artifacts.build(
            "base-frontend", base / "Dockerfile.frontend", base, source_sha=BASE
        )
        old = artifacts.manifest(1, old_backend, old_frontend, None, root=base, source_sha=BASE)
        old_ref = artifacts.registry.publish(artifacts.repository + "/releases", old)
        artifacts.registry.verify_images(old, pull=True)
        for reference in old.vendor_images.values():
            command(["docker", "pull", "--platform", "linux/amd64", reference])

        backend = artifacts.build("head-backend", ROOT / "Dockerfile.backend", ROOT)
        frontend = artifacts.build("head-frontend", ROOT / "Dockerfile.frontend", ROOT)
        candidate = artifacts.manifest(2, backend, frontend, old_ref)
        candidate_ref = artifacts.registry.publish(artifacts.repository + "/releases", candidate)

        stack = RolloutStack(tmp_path / "stack", old, root=base)
        stack.start()
        store = StateStore(tmp_path / "state")
        journal = SmokeJournal(tmp_path / "smoke-transaction.json")
        student = SmokeCredentials(stack.student_email, stack.student_password, "CAREER_INTERNSHIP")
        admin = SmokeCredentials(
            "khue.nguyen@demo.example.invalid", stack.seed_passwords["ADMIN"], "CAREER_INTERNSHIP"
        )

        def smoke(runtime: ReleaseRuntime) -> list[str]:
            with SmokeSession(runtime.origin, runtime.deadline) as session:
                return business_smoke(
                    session,
                    student,
                    runtime.effects,
                    journal=journal,
                    persisted_status=runtime.appointment_status,
                )

        def host(root, sha, port):
            selections = {"http_port": port}
            runtime = ReleaseRuntime(
                stack.compose_base(root),
                stack.env,
                stack.origin,
                stack.project,
                tmp_path / "rendered",
                root=root,
            )
            return RolloutController(
                store,
                artifacts.registry,
                runtime,
                sha,
                demo._configuration_identity("local", False, selections, root=root),
                smoke=smoke,
                transition=reviewed_transition("local", False, selections, root=root),
            )

        original = host(base, BASE, stack.port)
        before = original.deploy(old_ref)
        assert before.operation is not None and before.operation.phase == "succeeded", before
        assert before.stable is not None and before.stable.reference == old_ref
        db_identity = original.runtime.database_identity()
        with SmokeSession(stack.origin, Deadline(20)) as session:
            session.login(student)
            payload = booking_payload(session, student)
            committed = session.request(
                "POST",
                "/api/v1/appointments",
                201,
                private=True,
                json=payload,
                key="transition-fixture-committed",
            ).json()

        with pytest.raises(RolloutError, match="incompatible-configuration"):
            host(ROOT, source, stack.port + 1).deploy(candidate_ref)
        assert store.load() == before

        upgraded = host(ROOT, source, stack.port)
        assert upgraded.transition is not None
        assert upgraded.transition.previous_release == old.configuration
        assert upgraded.transition.previous_host_sha256 == before.configuration_sha256
        after = upgraded.deploy(candidate_ref)
        assert after.operation is not None and after.operation.phase == "succeeded", after
        assert after.stable is not None and after.stable.reference == candidate_ref
        assert after.previous_stable == before.stable
        assert (
            after.configuration_sha256
            == upgraded.configuration_sha256
            != before.configuration_sha256
        )
        upgraded.runtime.require_database(db_identity)
        assert upgraded.runtime.appointment_status(committed["id"]) == "UPCOMING"

        with SmokeSession(stack.origin, Deadline(20)) as session:
            session.login(admin)
            path = "/api/v1/admin/booking-configuration"
            policy = session.request("GET", path, 200, private=True).json()
            revision = policy.pop("updated_at")
            assert policy["windows"]
            policy["windows"][0]["opens_at"] = "00:00:00"
            policy["booking_horizon_days"] = 368
            written = session.request(
                "PUT", path, 200, private=True, json={**policy, "expected_updated_at": revision}
            ).json()
            assert written["updated_at"] != revision
            assert written["booking_horizon_days"] == 368
            assert written["windows"] == policy["windows"]

        restored = host(ROOT, source, stack.port)
        result = restored.rollback()
        assert result.operation is not None and result.operation.phase == "rolled_back", result
        assert result.stable == before.stable
        assert result.configuration_sha256 == after.configuration_sha256
        assert restored.runtime.manifest == old
        restored.runtime.require_database(db_identity)
        assert restored.runtime.appointment_status(committed["id"]) == "UPCOMING"
    finally:
        try:
            if stack is not None:
                stack.cleanup()
        finally:
            artifacts.close()
