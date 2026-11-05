"""Allowlisted operation results, distinct from historical deployment evidence v1/v2."""

from typing import Literal

from pydantic import Field, model_validator
from tools.deploy.release_manifest import SHA, Digest, Hex, Reference, Strict
from tools.deploy.rollout_state import HostState, Reason, utc


class Result(Strict):
    format: Literal["prl-rollout-result"] = "prl-rollout-result"
    format_version: Literal[1] = 1
    command: Literal["deploy", "rollback", "status"]
    outcome: Literal["succeeded", "rolled_back", "failed", "interrupted", "pending", "empty"]
    reason: Reason | None = None
    state: HostState | None = None


def result(command: Literal["deploy", "rollback", "status"], state: HostState | None) -> Result:
    if state is None:
        return Result(command=command, outcome="empty")
    if state.refusal is not None:
        return Result(command=command, outcome="failed", reason=state.refusal.reason, state=state)
    op = state.operation
    if op is None:
        return Result(command=command, outcome="empty", state=state)
    phase = op.phase
    outcomes: dict[str, Literal["succeeded", "rolled_back", "failed", "interrupted"]] = {
        "succeeded": "succeeded",
        "rolled_back": "rolled_back",
        "failed": "failed",
        "interrupted": "interrupted",
    }
    outcome = outcomes.get(phase)
    if outcome is not None:
        return Result(command=command, outcome=outcome, reason=op.events[-1].reason, state=state)
    return Result(command=command, outcome="pending", state=state)


class LabReport(Strict):
    format: Literal["prl-rollback-lab"]
    format_version: Literal[1]
    proof_kind: Literal["automated-linux-compose-lab"]
    fixture_review_metadata: Literal[True]
    source_sha: SHA
    pr_head_sha: SHA
    stable_manifest: Reference
    bad_manifest: Reference
    configuration_sha256: Hex
    rollback_decided_at: str
    readiness_completed_at: str
    smoke_completed_at: str
    rollback_seconds: float = Field(ge=0, le=300, allow_inf_nan=False)
    restored_images: dict[Literal["backend", "frontend", "collector"], Reference]
    restored_image_ids: dict[Literal["backend", "frontend", "collector"], Digest]
    bad_backend_image_id: Digest
    committed_before: Literal[1]
    committed_during: Literal[1]
    committed_records_preserved: Literal[True]
    replay_and_effects_verified: Literal[True]
    concurrent_rollback_refused: Literal[True]
    negative_preflight_cases: dict[Literal["missing_images", "incompatible_configuration"], Reason]
    database_volume_preserved: Literal[True]
    result: HostState
    outcome: Literal["passed"]

    @model_validator(mode="after")
    def complete(self):
        op, stable = self.result.operation, self.result.stable
        if (
            op is None
            or stable is None
            or op.phase != "rolled_back"
            or op.command != "deploy"
            or op.orchestrator_sha != self.source_sha
            or op.trigger_reason != "smoke-failed"
            or op.target.reference != self.bad_manifest
            or stable.reference != self.stable_manifest
            or op.fallback != stable
            or self.result.refusal is not None
            or self.restored_images != stable.images
            or self.restored_image_ids != stable.image_ids
            or self.configuration_sha256 != stable.configuration_sha256
            or self.bad_backend_image_id != op.target.image_ids["backend"]
            or self.bad_backend_image_id == self.restored_image_ids["backend"]
            or self.negative_preflight_cases
            != {
                "missing_images": "missing-images",
                "incompatible_configuration": "incompatible-configuration",
            }
        ):
            raise ValueError("Incomplete rollback lab proof")
        decided = next(e for e in op.events if e.phase == "rollback_preflight")
        ready = next(e for e in op.events if e.phase == "rollback_smoke")
        if (
            self.rollback_decided_at != decided.at
            or self.readiness_completed_at != ready.at
            or self.smoke_completed_at != op.events[-1].at
            or abs(
                self.rollback_seconds - (op.events[-1].elapsed_seconds - decided.elapsed_seconds)
            )
            > 1e-6
            or not utc(decided.at) <= utc(ready.at) <= utc(op.events[-1].at)
        ):
            raise ValueError("Invalid rollback lab timeline")
        return self


class LabFailure(Strict):
    format: Literal["prl-rollback-lab-failure"]
    format_version: Literal[1]
    source_sha: SHA
    outcome: Literal["failed"]
    stage: Literal[
        "artifacts",
        "fixture_start",
        "stable_deployment",
        "negative_preflight_cases",
        "bad_deployment_and_rollback",
        "committed_data_verification",
    ]
    reason: Reason | Literal["fixture-failed"]
    state: HostState | None
