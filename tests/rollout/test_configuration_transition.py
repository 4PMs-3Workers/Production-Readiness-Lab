"""Upgrade a running predecessor with its retained manifest and MySQL volume."""

import json
import os
import tarfile

import pytest
from tests.rollout.fixtures import Artifacts, RolloutStack
from tools.deploy import demo
from tools.deploy.release_images import command
from tools.deploy.release_manifest import ROOT, SCHEMA, canonical
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

FIXED_AVAILABILITY = """
import asyncio
import json
import sys
from datetime import UTC, datetime
from urllib.parse import urlencode

from app.dependencies.clock import get_clock
from app.factory import create_app

async def main():
    request = json.load(sys.stdin)
    app = create_app()
    app.dependency_overrides[get_clock] = lambda: lambda: datetime(2027, 3, 13, 16, tzinfo=UTC)
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(event):
        sent.append(event)
    async with app.router.lifespan_context(app):
        await app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/api/v1/availability", "query_string": urlencode(request["params"]).encode(),
            "headers": [(b"cookie", ("prl_session=" + request["session"]).encode())],
            "client": ("127.0.0.1", 50000), "server": ("localhost", 80),
        }, receive, send)
    body = b"".join(
        event.get("body", b"") for event in sent if event["type"] == "http.response.body"
    )
    print(json.dumps({"status": sent[0]["status"], "body": json.loads(body)}))

asyncio.run(main())
"""


def fixed_availability(runtime, stack, backend, payload, session):
    raw = runtime.command(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--pull",
            "never",
            "--platform",
            "linux/amd64",
            "--network",
            f"{stack.project}_data",
            "--env-file",
            stack.env_files["PRL_RUNTIME_ENV_FILE"],
            "--env",
            f"ACCEPTED_SCHEMA_REVISIONS={SCHEMA}",
            "--entrypoint",
            "python",
            backend.reference,
            "-c",
            FIXED_AVAILABILITY,
        ],
        cap=30,
        input_data=canonical(
            {
                "session": session,
                "params": {key: payload[key] for key in ("purpose_id", "staff_id")}
                | {"date": "2027-03-14"},
            }
        ),
    )
    return json.loads(raw.splitlines()[-1])


def running_images(runtime: ReleaseRuntime) -> dict[str, tuple[str, str]]:
    images = {}
    for service in ("api", "worker", "frontend", "gateway"):
        record = runtime.inspect(service)
        assert record is not None and record["running"]
        images[service] = (record["id"], record["image"])
    return images


def test_reviewed_upgrade_preserves_host_and_refuses_incompatible_rollback(tmp_path):
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

            purpose_path = f"/api/v1/admin/purposes/{payload['purpose_id']}"
            purpose = session.request("GET", purpose_path, 200, private=True).json()
            purpose_update = {
                key: purpose[key] for key in ("name", "requires_free_text", "is_active")
            }
            session.request(
                "PUT",
                purpose_path,
                200,
                private=True,
                json={
                    **purpose_update,
                    "duration_minutes": 30,
                    "expected_updated_at": purpose["updated_at"],
                },
            )
            dst_policy = {
                **policy,
                "timezone": "America/New_York",
                "windows": [
                    {"weekday": 7, "opens_at": "02:30:00", "closes_at": "03:30:00"},
                    {"weekday": 7, "opens_at": "09:00:00", "closes_at": "12:00:00"},
                ],
                "expected_updated_at": written["updated_at"],
            }
            saved = session.request("PUT", path, 200, private=True, json=dst_policy).json()
            assert saved["windows"] == dst_policy["windows"]

            with SmokeSession(stack.origin, Deadline(20)) as student_session:
                student_session.login(student)
                token = student_session.client.cookies.get("prl_session")
                assert token
                new_slots = fixed_availability(upgraded.runtime, stack, backend, payload, token)
                assert new_slots["status"] == 200
                starts = [slot["starts_at"] for slot in new_slots["body"]["slots"]]
                assert starts and all(
                    "2027-03-14T13:00:00Z" <= start <= "2027-03-14T15:30:00Z" for start in starts
                )
                old_slots = fixed_availability(upgraded.runtime, stack, old_backend, payload, token)
                assert old_slots["status"] == 503
                assert old_slots["body"]["error"]["code"] == "SERVICE_UNAVAILABLE"

            running = running_images(upgraded.runtime)
            refused = host(ROOT, source, stack.port).rollback()
            assert refused.operation is not None
            assert [event.phase for event in refused.operation.events] == [
                "rollback_preflight",
                "failed",
            ]
            assert refused.operation.events[-1].reason == "schema-incompatible"
            assert refused.stable == after.stable and refused.previous_stable == before.stable
            assert running_images(upgraded.runtime) == running
            upgraded.runtime.require_database(db_identity)
            assert session.request("GET", path, 200, private=True).json() == saved

            fixed = session.request(
                "PUT",
                path,
                200,
                private=True,
                json={**policy, "expected_updated_at": saved["updated_at"]},
            ).json()
            assert fixed["windows"] == policy["windows"]

        running = running_images(upgraded.runtime)
        restored = host(ROOT, source, stack.port)
        result = restored.rollback()
        assert result.operation is not None
        assert [event.phase for event in result.operation.events] == [
            "rollback_preflight",
            "failed",
        ]
        assert result.operation.events[-1].reason == "schema-incompatible"
        assert result.stable == after.stable and result.previous_stable == before.stable
        assert running_images(upgraded.runtime) == running
        upgraded.runtime.require_database(db_identity)
        assert upgraded.runtime.appointment_status(committed["id"]) == "UPCOMING"
    finally:
        try:
            if stack is not None:
                stack.cleanup()
        finally:
            artifacts.close()
