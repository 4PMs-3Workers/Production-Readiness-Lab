"""Real container preflight refusals and controller contention, without raw diagnostics."""

from __future__ import annotations

import hashlib
import subprocess
import sys

from tools.deploy.release_manifest import Manifest, canonical, configuration, parse
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_state import RolloutError


def concurrent_rollback_is_refused(controller: RolloutController) -> None:
    # Called from the actual deployment's rollout hook, while its kernel lock is
    # held. The competing process cannot execute Docker even if a lock regresses.
    script = """
import sys
from pathlib import Path
from tools.deploy.release_images import Registry
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_runtime import ReleaseRuntime
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore
class NoDocker(ReleaseRuntime):
    def command(self,*args,**kwargs):
        raise AssertionError('Concurrent rollback reached Docker')
runtime=NoDocker([],{},'http://localhost',sys.argv[2],Path(sys.argv[1])/'never-used')
host=RolloutController(StateStore(Path(sys.argv[1])),Registry(),runtime,'a'*40,sys.argv[3],
                      smoke=lambda r:[])
try:
    host.rollback()
except RolloutError as e:
    print(e.reason)
    raise SystemExit(0 if e.reason=='operation-locked' else 2)
raise SystemExit(3)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(controller.store.directory),
            controller.runtime.project,
            controller.configuration_sha256,
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip() != b"operation-locked":
        raise RolloutError("operation-locked")


def preflight_refusals(
    controller: RolloutController, artifacts, stable_ref: str, stable: Manifest
) -> dict:
    before = controller.store.load()
    if before is None or before.stable is None:
        raise RolloutError("invalid-state")
    actual = controller.runtime.database_identity()
    observed = {}
    missing = stable.images["backend"].model_copy(
        update={
            "reference": artifacts.repository + "/absent@sha256:" + "f" * 64,
            "image_id": "sha256:" + "f" * 64,
        }
    )
    missing_manifest = artifacts.manifest(3, missing, stable.images["frontend"], stable_ref)
    missing_ref = artifacts.registry.publish(artifacts.repository + "/releases", missing_manifest)
    rejected = controller.deploy(missing_ref)
    if rejected.operation is None or rejected.operation.events[-1].reason != "missing-images":
        raise RolloutError("invalid-state")
    observed["missing_images"] = "missing-images"

    mismatched = artifacts.manifest(
        4, stable.images["backend"], stable.images["frontend"], stable_ref
    )
    data = mismatched.model_dump(mode="json")
    config = configuration("local", False).model_dump(mode="json")
    config["files"][0]["sha256"] = "f" * 64
    config["sha256"] = hashlib.sha256(
        canonical({k: v for k, v in config.items() if k != "sha256"})
    ).hexdigest()
    data["configuration"] = config
    data["candidate_proof"]["configuration_sha256"] = config["sha256"]
    data["published_proof"]["configuration_sha256"] = config["sha256"]
    wrong = parse(canonical(data), Manifest)
    wrong_ref = artifacts.registry.publish(artifacts.repository + "/releases", wrong)
    rejected = controller.deploy(wrong_ref)
    if (
        rejected.operation is None
        or rejected.operation.events[-1].reason != "incompatible-configuration"
    ):
        raise RolloutError("invalid-state")
    observed["incompatible_configuration"] = "incompatible-configuration"
    current = controller.store.load()
    if current is None or current.stable != before.stable:
        raise RolloutError("invalid-state")
    controller.runtime.select(stable)
    controller.runtime.require_database(actual)
    controller.runtime.verify_images()
    return observed
