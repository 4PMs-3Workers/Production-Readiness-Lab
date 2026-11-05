"""Allowlisted operation results, distinct from historical deployment evidence v1/v2."""

from typing import Literal

from tools.deploy.release_manifest import Strict
from tools.deploy.rollout_state import HostState, Reason


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
