"""Callable isolated Linux rollback drill; only allowlisted evidence is public."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import UUID, uuid4

from tests.rollout.fixtures import Artifacts, RolloutStack
from tests.rollout.gates import concurrent_rollback_is_refused, preflight_refusals
from tools.deploy.release_images import command
from tools.deploy.release_manifest import ROOT, canonical
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_http import SmokeCredentials, SmokeSession
from tools.deploy.rollout_readiness import ready
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime
from tools.deploy.rollout_smoke import booking_payload, business_smoke
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


def prerequisites() -> str:
    if sys.platform != "linux" or any(
        shutil.which(name) is None for name in ("docker", "skopeo", "oras", "git")
    ):
        raise RuntimeError("Required Linux release rehearsal prerequisites are unavailable")
    if command(["git", "status", "--porcelain", "--untracked-files=all"]):
        raise RuntimeError("A clean committed rehearsal checkout is required")
    return command(["git", "rev-parse", "HEAD"]).decode().strip()


def rehearse(directory: Path) -> dict:
    """Incident-drill harnesses can call this without shelling out or changing prl-demo."""
    source = prerequisites()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = directory / ("private-" + uuid4().hex)
    private.mkdir(mode=0o700)
    artifacts = Artifacts(private / "artifacts", source)
    stack, store = None, None
    stage = "artifacts"
    try:
        artifacts.start()
        stable_ref, stable, bad_ref, bad = artifacts.releases(ROOT)
        artifacts.registry.verify_images(stable, pull=True)
        for reference in stable.vendor_images.values():
            command(["docker", "pull", "--platform", "linux/amd64", reference])
        stage = "fixture_start"
        stack = RolloutStack(private / "stack", stable)
        stack.start()
        store = StateStore(private / "state")
        account = SmokeCredentials(stack.student_email, stack.student_password, "CAREER_INTERNSHIP")
        committed: list[tuple[dict, str, dict]] = []
        during = [False]

        with SmokeSession(stack.origin, Deadline(1200)) as actor:
            actor.login(account)

            def book() -> None:
                payload, key = booking_payload(actor, account), uuid4().hex
                receipt = actor.request(
                    "POST", "/api/v1/appointments", 201, key=key, private=True, json=payload
                ).json()
                UUID(receipt["id"])
                committed.append((payload, key, receipt))

            class Runtime(ReleaseRuntime):
                def rollout(self) -> None:
                    if self.manifest is not None and self.manifest.release_id == bad.release_id:
                        # A real HTTP transaction commits while the rollout operation
                        # owns its lock, immediately before graceful app quiescence.
                        book()
                        concurrent_rollback_is_refused(controller)
                        during[0] = True
                    super().rollout()

            runtime = Runtime(
                stack.base, stack.env, stack.origin, stack.project, private / "rendered"
            )

            def smoke(selected: ReleaseRuntime) -> list[str]:
                with SmokeSession(selected.origin, selected.deadline) as session:
                    return business_smoke(session, account, selected.effects)

            host_config = hashlib.sha256(
                canonical(
                    dict(
                        manifest_configuration=stable.configuration.sha256,
                        project=stack.project,
                        origin=stack.origin,
                        isolated_subnet="172.30.45.0/29",
                    )
                )
            ).hexdigest()
            controller = RolloutController(
                store,
                artifacts.registry,
                runtime,
                source,
                host_config,
                smoke=smoke,
                readiness=ready,
            )
            stage = "stable_deployment"
            first = controller.deploy(stable_ref)
            if first.operation is None or first.operation.phase != "succeeded":
                raise RolloutError("smoke-failed")
            before_volume = runtime.database_identity()
            stage = "negative_preflight_cases"
            negative_cases = preflight_refusals(controller, artifacts, stable_ref, stable)
            book()
            stage = "bad_deployment_and_rollback"
            restored = controller.deploy(bad_ref)
            if (
                restored.operation is None
                or restored.operation.phase != "rolled_back"
                or restored.operation.trigger_reason != "smoke-failed"
                or restored.stable != first.stable
                or not during[0]
                or len(committed) != 2
            ):
                raise RolloutError("rollback-failed")
            if runtime.database_identity() != before_volume:
                raise RolloutError("volume-changed")
            runtime.verify_images()
            stage = "committed_data_verification"
            for payload, key, receipt in committed:
                appointment = str(UUID(receipt["id"]))
                actor.request("GET", f"/api/v1/appointments/{appointment}", 200, private=True)
                replay = actor.request(
                    "POST", "/api/v1/appointments", 200, key=key, private=True, json=payload
                ).json()
                if replay != receipt or runtime.effects(appointment) != (1, 1, 2):
                    raise RolloutError("smoke-failed")
            actor.request("POST", "/api/v1/auth/logout", 204)
        assert restored.stable is not None
        op = restored.operation
        decided = next(e for e in op.events if e.phase == "rollback_preflight")
        smoke_started = next(e for e in op.events if e.phase == "rollback_smoke")
        elapsed = op.events[-1].elapsed_seconds - decided.elapsed_seconds
        if not 0 <= elapsed <= 300:
            raise RolloutError("deadline-exceeded")
        report = dict(
            format="prl-rollback-lab",
            format_version=1,
            proof_kind="automated-linux-compose-lab",
            fixture_review_metadata=True,
            source_sha=source,
            pr_head_sha=os.environ.get("PR_HEAD_SHA") or source,
            stable_manifest=stable_ref,
            bad_manifest=bad_ref,
            configuration_sha256=stable.configuration.sha256,
            rollback_decided_at=decided.at,
            readiness_completed_at=smoke_started.at,
            smoke_completed_at=op.events[-1].at,
            rollback_seconds=elapsed,
            restored_images=restored.stable.images,
            restored_image_ids=restored.stable.image_ids,
            bad_backend_image_id=bad.images["backend"].image_id,
            committed_before=1,
            committed_during=1,
            committed_records_preserved=True,
            replay_and_effects_verified=True,
            concurrent_rollback_refused=True,
            negative_preflight_cases=negative_cases,
            database_volume_preserved=True,
            result=restored.model_dump(mode="json"),
            outcome="passed",
        )
        (directory / "rehearsal.json").write_bytes(canonical(report))
        return report
    except Exception as error:
        state = store.load() if store is not None else None
        failure = dict(
            format="prl-rollback-lab-failure",
            format_version=1,
            source_sha=source,
            outcome="failed",
            stage=stage,
            reason=error.reason if isinstance(error, RolloutError) else "fixture-failed",
            state=state.model_dump(mode="json") if state else None,
        )
        (directory / "failure.json").write_bytes(canonical(failure))
        raise
    finally:
        try:
            if stack is not None:
                stack.cleanup()
        finally:
            artifacts.close()


def main() -> int:
    try:
        report = rehearse(ROOT / "build/rollback-review")
        print(
            json.dumps(dict(outcome=report["outcome"], rollback_seconds=report["rollback_seconds"]))
        )
        return 0
    except Exception:
        print('{"outcome":"failed","reason":"rollback-rehearsal-failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
