from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ProbeResult:
    check_name: str
    success: bool
    status_code: int | None
    outcome: str


@dataclass(frozen=True)
class CheckState:
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    firing: bool = False
    notified_firing: bool = False
    transition_sequence: int = 0
    last_attempt_at: str | None = None
    last_evaluation_at: str | None = None
    last_resolution_at: str | None = None


@dataclass(frozen=True)
class Transition:
    event_id: str
    check_name: str
    firing: bool
    occurred_at: str


def evaluate(
    results: tuple[ProbeResult, ...] | list[ProbeResult],
    states: dict[str, CheckState],
    *,
    failure_threshold: int,
    recovery_threshold: int,
    now: datetime,
    attempted_at: datetime | None = None,
) -> tuple[tuple[Transition, ...], dict[str, CheckState]]:
    if not results:
        raise ValueError("At least one probe result is required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Evaluation time must be timezone-aware")
    if attempted_at is not None and (
        attempted_at.tzinfo is None or attempted_at.utcoffset() is None
    ):
        raise ValueError("Probe attempt time must be timezone-aware")
    if not 1 <= failure_threshold <= 10 or not 1 <= recovery_threshold <= 10:
        raise ValueError("Evaluation thresholds are invalid")
    instant = now.isoformat(timespec="milliseconds")
    attempt_instant = (attempted_at or now).isoformat(timespec="milliseconds")
    updated = dict(states)
    transitions: list[Transition] = []
    for result in results:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", result.check_name):
            raise ValueError("Probe result is invalid")
        if not isinstance(result.success, bool) or result.outcome not in {
            "ok",
            "upstream_error",
            "response_too_large",
            "timeout",
            "tls_untrusted",
            "network_error",
        }:
            raise ValueError("Probe result is invalid")
        previous = updated.get(result.check_name, CheckState())
        failures = (
            0 if result.success else min(failure_threshold, previous.consecutive_failures + 1)
        )
        successes = (
            min(recovery_threshold, previous.consecutive_successes + 1) if result.success else 0
        )
        firing = previous.firing
        sequence = previous.transition_sequence
        resolution_at = previous.last_resolution_at
        if not firing and failures >= failure_threshold:
            firing = True
            sequence += 1
        elif firing and successes >= recovery_threshold:
            firing = False
            sequence += 1
            resolution_at = instant
        updated[result.check_name] = CheckState(
            consecutive_failures=failures,
            consecutive_successes=successes,
            firing=firing,
            notified_firing=previous.notified_firing,
            transition_sequence=sequence,
            last_attempt_at=attempt_instant,
            last_evaluation_at=instant,
            last_resolution_at=resolution_at,
        )
        if firing != previous.firing:
            event_id = hashlib.sha256(
                f"{result.check_name}:{sequence}:{int(firing)}:{instant}".encode()
            ).hexdigest()
            transitions.append(Transition(event_id, result.check_name, firing, instant))
    return tuple(transitions), updated
