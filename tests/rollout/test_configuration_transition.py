"""Upgrade a running predecessor with its retained manifest and MySQL volume."""

import json
import os
import subprocess
import sys
import tarfile
from uuid import uuid4

import pytest
from tests.rollout.fixtures import Artifacts, RolloutStack
from tools.deploy import demo
from tools.deploy.release_images import command
from tools.deploy.release_manifest import (
    PREDECESSOR_SCHEMA,
    ROOT,
    SCHEMA,
    canonical,
)
from tools.deploy.rollout import reviewed_transition
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_readiness import ready
from tools.deploy.rollout_rehearsal import prerequisites
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime
from tools.deploy.rollout_schema import require_schema
from tools.deploy.rollout_smoke import booking_payload, business_smoke
from tools.deploy.rollout_smoke_journal import SmokeJournal
from tools.deploy.rollout_state import (
    Event,
    HostState,
    Operation,
    RolloutError,
    Verification,
    timestamp,
)
from tools.deploy.rollout_store import StateStore

# Build exactly the release before the account lifecycle schema and gateway changes.
BASE = "12a74f7308550ccc294a783edde79db267a526d1"

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


@pytest.mark.parametrize(
    "stable_ref,stable_schema",
    [
        (BASE, PREDECESSOR_SCHEMA),
        ("aed6c68a4a081f558d01e9ba4c39cd031439af47", SCHEMA),
    ],
)
def test_reviewed_configuration_upgrade_and_rollback_preserve_the_existing_host(
    tmp_path, stable_ref, stable_schema
):
    assert os.environ.get("RUN_ROLLOUT_REHEARSAL") == "1", "Required rollout rehearsal cannot skip"
    source = prerequisites()
    archive = tmp_path / "base.tar"
    base = tmp_path / "base"
    base.mkdir()
    command(["git", "archive", "--format=tar", "-o", str(archive), stable_ref], cwd=ROOT)
    with tarfile.open(archive) as tree:
        tree.extractall(base, filter="data")

    artifacts = Artifacts(tmp_path / "artifacts", source)
    stack = None
    try:
        artifacts.start()
        old_backend = artifacts.build(
            "base-backend", base / "Dockerfile.backend", base, source_sha=stable_ref
        )
        old_frontend = artifacts.build(
            "base-frontend", base / "Dockerfile.frontend", base, source_sha=stable_ref
        )
        old = artifacts.manifest(
            1,
            old_backend,
            old_frontend,
            None,
            root=base,
            source_sha=stable_ref,
            schema_revision=stable_schema,
        )
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

        def host(root, sha, port, readiness=ready):
            selections = {"http_port": port}
            runtime = ReleaseRuntime(
                stack.compose_base(root),
                stack.env,
                stack.origin,
                stack.project,
                tmp_path / "rendered",
            )
            return RolloutController(
                store,
                artifacts.registry,
                runtime,
                sha,
                demo._configuration_identity("local", False, selections, root=root),
                smoke=smoke,
                readiness=readiness,
                transition_resolver=lambda state: reviewed_transition(
                    "local",
                    False,
                    selections,
                    root=root,
                    previous_host_sha256=state.configuration_sha256,
                    stable_configuration=(
                        store.manifest(state.stable).configuration if state.stable else None
                    ),
                ),
            )

        original = host(base, stable_ref, stack.port)
        original.runtime.select(old)
        require_schema(original.runtime.schema(), [stable_schema])
        db_identity = original.runtime.database_identity()
        ready(original.runtime)
        checks = smoke(original.runtime)
        stable = store.remember(old_ref, old)
        started_at = timestamp()
        before = HostState(
            project=stack.project,
            configuration_sha256=original.configuration_sha256,
            stable=stable,
            operation=Operation(
                operation_id=uuid4().hex,
                command="deploy",
                orchestrator_sha=stable_ref,
                target=stable,
                events=[
                    Event(phase=phase, at=started_at, elapsed_seconds=0)
                    for phase in (
                        "preflight",
                        "migration",
                        "rollout",
                        "readiness",
                        "smoke",
                        "succeeded",
                    )
                ],
                verification=Verification(
                    schema_revision=stable_schema,
                    image_identity_verified=True,
                    database_volume_preserved=True,
                    worker_progress_verified=True,
                    smoke_checks=checks,
                ),
            ),
        )
        # Persist the historical record through the archived predecessor's validator and writer.
        historical_state = before.model_dump(mode="json")
        historical_state["operation"].pop("migration_source")
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; "
                "from pathlib import Path; "
                "from tools.deploy.release_manifest import parse; "
                "from tools.deploy.rollout_state import HostState; "
                "from tools.deploy.rollout_store import StateStore; "
                "StateStore(Path(sys.argv[1])).save(parse(sys.stdin.buffer.read(), HostState))",
                str(store.directory),
            ],
            input=canonical(historical_state),
            cwd=base,
            env={**os.environ, "PYTHONPATH": str(base / "src/backend")},
            check=True,
            timeout=20,
        )
        assert store.load() == before
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

        def reject_candidate(runtime):
            if runtime.manifest == candidate:
                require_schema(runtime.schema(), [SCHEMA])
                raise RolloutError("readiness-failed")
            ready(runtime)

        if stable_schema == PREDECESSOR_SCHEMA:
            interrupted = host(ROOT, source, stack.port, readiness=reject_candidate)

            def crash_before_migration():
                raise KeyboardInterrupt()

            interrupted.runtime.migrate = crash_before_migration
            with pytest.raises(KeyboardInterrupt):
                interrupted.deploy(candidate_ref)
            persisted = store.load()
            assert (
                persisted is not None
                and persisted.configuration_sha256 == upgraded.configuration_sha256
            )
            require_schema(interrupted.runtime.schema(), [PREDECESSOR_SCHEMA])
            failed = host(ROOT, source, stack.port, readiness=reject_candidate).deploy(
                candidate_ref, resume=True
            )
        else:
            failed = host(ROOT, source, stack.port, readiness=reject_candidate).deploy(
                candidate_ref
            )
        assert failed.operation is not None and failed.operation.phase == "rolled_back", failed
        assert failed.operation.trigger_reason == "readiness-failed"
        assert failed.operation.verification is not None
        assert failed.operation.verification.schema_revision == stable_schema
        assert failed.stable == before.stable
        upgraded.runtime.select(old)
        require_schema(upgraded.runtime.schema(), [stable_schema])
        upgraded.runtime.require_database(db_identity)
        assert upgraded.runtime.appointment_status(committed["id"]) == "UPCOMING"

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
                assert old_slots["status"] == 200
                assert [slot["starts_at"] for slot in old_slots["body"]["slots"]] == starts

            if stable_schema == SCHEMA:
                restored = host(ROOT, source, stack.port)
                result = restored.rollback()
                assert result.operation is not None and result.operation.phase == "rolled_back"
                assert result.stable == before.stable
                restored.runtime.require_database(db_identity)
                require_schema(restored.runtime.schema(), [SCHEMA])
                assert restored.runtime.appointment_status(committed["id"]) == "UPCOMING"
                assert session.request("GET", path, 200, private=True).json() == saved
                return

            running = running_images(upgraded.runtime)
            refused = host(ROOT, source, stack.port).rollback()
            assert refused.operation == after.operation
            assert refused.refusal is not None and refused.refusal.reason == "rollback-incompatible"
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

        restored = host(ROOT, source, stack.port)
        result = restored.rollback()
        assert result.operation is not None and result.operation.phase == "rolled_back", result
        assert result.stable == before.stable
        assert result.configuration_sha256 == after.configuration_sha256
        assert restored.runtime.manifest == old
        assert result.operation.verification is not None
        assert result.operation.verification.schema_revision == stable_schema
        require_schema(restored.runtime.schema(), [stable_schema])
        restored.runtime.require_database(db_identity)
        assert restored.runtime.appointment_status(committed["id"]) == "UPCOMING"
    finally:
        try:
            if stack is not None:
                stack.cleanup()
        finally:
            artifacts.close()
