from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .evaluator import CheckState

_CHECK_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_EVENT_ID = re.compile(r"[0-9a-f]{64}\Z")
_HEALTH = {"unknown", "healthy", "degraded"}
_MAX_CHECK_STATES = 80  # Pending checks, deferred resolutions, and current configured checks.


@dataclass(frozen=True)
class PendingNotification:
    event_id: str
    check_name: str
    firing: bool
    occurred_at: str
    attempts: int = 0
    next_attempt_at: str | None = None


@dataclass(frozen=True)
class MonitorState:
    checks: dict[str, CheckState] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    status_codes: dict[str, int | None] = field(default_factory=dict)
    pending: tuple[PendingNotification, ...] = ()
    last_probe_started_at: str | None = None
    last_probe_completed_at: str | None = None
    last_evaluation_at: str | None = None
    last_delivery_attempt_at: str | None = None
    last_delivery_success_at: str | None = None
    last_resolution_at: str | None = None
    probe_attempts_total: int = 0
    evaluations_total: int = 0
    delivery_attempts_total: int = 0
    last_delivery_outcome: str = "unknown"
    probe_health: str = "unknown"
    delivery_health: str = "unknown"


def _validate_timestamp(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _check_counter(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1


class StateStore:
    def __init__(self, path: Path, *, max_bytes: int, max_pending: int) -> None:
        if not 4096 <= max_bytes <= 1_048_576 or not 1 <= max_pending <= 64:
            raise ValueError("Monitor state limits are invalid")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_pending = max_pending
        self.max_check_states = _MAX_CHECK_STATES

    @classmethod
    def worst_case_bytes(cls, max_pending: int) -> int:
        """Size the largest valid bounded state before accepting its byte budget."""
        if not 1 <= max_pending <= 64:
            raise ValueError("Monitor state limits are invalid")
        # A valid non-ASCII datetime separator maximizes JSON's escaped representation.
        timestamp = "9999-12-31\U0001f4a923:59:59.999999+23:59:59.9999"
        names = tuple(f"check{index:03d}".ljust(32, "x") for index in range(_MAX_CHECK_STATES))
        counter = 2**63 - 1
        checks = {
            name: CheckState(
                consecutive_failures=counter,
                consecutive_successes=counter,
                firing=True,
                notified_firing=True,
                transition_sequence=counter,
                last_attempt_at=timestamp,
                last_evaluation_at=timestamp,
                last_resolution_at=timestamp,
            )
            for name in names
        }
        state = MonitorState(
            checks=checks,
            outcomes={name: "response_too_large" for name in names},
            status_codes={name: None for name in names},
            pending=tuple(
                PendingNotification(
                    f"{index + 1:064x}",
                    names[index],
                    True,
                    timestamp,
                    attempts=counter,
                    next_attempt_at=timestamp,
                )
                for index in range(max_pending)
            ),
            last_probe_started_at=timestamp,
            last_probe_completed_at=timestamp,
            last_evaluation_at=timestamp,
            last_delivery_attempt_at=timestamp,
            last_delivery_success_at=timestamp,
            last_resolution_at=timestamp,
            probe_attempts_total=counter,
            evaluations_total=counter,
            delivery_attempts_total=counter,
            last_delivery_outcome="network_error",
            probe_health="degraded",
            delivery_health="degraded",
        )
        store = cls(Path("state.json"), max_bytes=1_048_576, max_pending=max_pending)
        raw = json.dumps(
            store._encode(state), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return len(raw)

    def load(self) -> MonitorState:
        try:
            with self.path.open("rb") as state_file:
                raw = state_file.read(self.max_bytes + 1)
        except FileNotFoundError:
            return MonitorState()
        except OSError:
            raise ValueError("Monitor state is invalid") from None
        if not raw or len(raw) > self.max_bytes:
            raise ValueError("Monitor state is invalid")
        try:
            data = json.loads(
                raw, object_pairs_hook=_strict_object, parse_constant=self._reject_constant
            )
            return self._decode(data)
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Monitor state is invalid") from None

    def save(self, state: MonitorState) -> None:
        try:
            data = self._encode(state)
            raw = json.dumps(
                data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Monitor state is invalid") from None
        if len(raw) > self.max_bytes:
            raise ValueError("Monitor state is invalid")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".monitor-state-", suffix=".tmp", dir=self.path.parent
            )
            try:
                fchmod = getattr(os, "fchmod", None)
                if fchmod is not None:
                    fchmod(descriptor, 0o600)
                else:
                    os.chmod(temporary_name, 0o600)
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(raw)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, self.path)
                if os.name == "posix":
                    directory_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
                raise
        except (OSError, TypeError, ValueError):
            raise ValueError("Monitor state could not be saved") from None

    @staticmethod
    def _reject_constant(_: str) -> None:
        raise ValueError("invalid number")

    def _encode(self, state: MonitorState) -> dict[str, Any]:
        if not isinstance(state, MonitorState) or len(state.checks) > _MAX_CHECK_STATES:
            raise ValueError("invalid state shape")
        if len(state.pending) > self.max_pending:
            raise ValueError("pending queue overflow")
        pending_ids: set[str] = set()
        if state.probe_health not in _HEALTH or state.delivery_health not in _HEALTH:
            raise ValueError("invalid health state")
        if state.last_delivery_outcome not in {
            "unknown",
            "accepted",
            "rejected",
            "timeout",
            "network_error",
            "invalid_event",
        }:
            raise ValueError("invalid delivery outcome")
        if (
            set(state.outcomes) - set(state.checks)
            or set(state.status_codes) - set(state.checks)
            or any(
                outcome
                not in {
                    "ok",
                    "upstream_error",
                    "response_too_large",
                    "timeout",
                    "tls_untrusted",
                    "network_error",
                }
                for outcome in state.outcomes.values()
            )
            or any(
                status is not None
                and (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not 100 <= status <= 599
                )
                for status in state.status_codes.values()
            )
        ):
            raise ValueError("invalid probe diagnostics")
        for name, check in state.checks.items():
            if not _CHECK_NAME.fullmatch(name) or not isinstance(check, CheckState):
                raise ValueError("invalid check state")
            if (
                not _check_counter(check.consecutive_failures)
                or not _check_counter(check.consecutive_successes)
                or not _check_counter(check.transition_sequence)
                or not isinstance(check.firing, bool)
                or not isinstance(check.notified_firing, bool)
                or not all(
                    _validate_timestamp(value)
                    for value in (
                        check.last_attempt_at,
                        check.last_evaluation_at,
                        check.last_resolution_at,
                    )
                )
            ):
                raise ValueError("invalid check state")
        pending: list[dict[str, Any]] = []
        for item in state.pending:
            if (
                not _EVENT_ID.fullmatch(item.event_id)
                or item.event_id in pending_ids
                or not _CHECK_NAME.fullmatch(item.check_name)
                or not isinstance(item.firing, bool)
                or not _validate_timestamp(item.occurred_at)
                or not _check_counter(item.attempts)
                or not _validate_timestamp(item.next_attempt_at)
            ):
                raise ValueError("invalid pending event")
            pending_ids.add(item.event_id)
            pending.append(asdict(item))
        counters = (
            state.probe_attempts_total,
            state.evaluations_total,
            state.delivery_attempts_total,
        )
        if not all(_check_counter(value) for value in counters):
            raise ValueError("invalid counters")
        timestamps = (
            state.last_probe_started_at,
            state.last_probe_completed_at,
            state.last_evaluation_at,
            state.last_delivery_attempt_at,
            state.last_delivery_success_at,
            state.last_resolution_at,
        )
        if not all(_validate_timestamp(value) for value in timestamps):
            raise ValueError("invalid timestamps")
        return {
            "schema": 1,
            "checks": {name: asdict(value) for name, value in state.checks.items()},
            "outcomes": state.outcomes,
            "status_codes": state.status_codes,
            "pending": pending,
            "timestamps": {
                "probe_started": state.last_probe_started_at,
                "probe_completed": state.last_probe_completed_at,
                "evaluation": state.last_evaluation_at,
                "delivery_attempt": state.last_delivery_attempt_at,
                "delivery_success": state.last_delivery_success_at,
                "resolution": state.last_resolution_at,
            },
            "counters": {
                "probe_attempts": state.probe_attempts_total,
                "evaluations": state.evaluations_total,
                "delivery_attempts": state.delivery_attempts_total,
            },
            "health": {"probe": state.probe_health, "delivery": state.delivery_health},
            "last_delivery_outcome": state.last_delivery_outcome,
        }

    def _decode(self, data: Any) -> MonitorState:
        if (
            not isinstance(data, dict)
            or set(data)
            != {
                "schema",
                "checks",
                "outcomes",
                "status_codes",
                "pending",
                "timestamps",
                "counters",
                "health",
                "last_delivery_outcome",
            }
            or data["schema"] != 1
        ):
            raise ValueError("invalid state shape")
        checks_raw, pending_raw = data["checks"], data["pending"]
        outcomes, status_codes = data["outcomes"], data["status_codes"]
        timestamps, counters, health = data["timestamps"], data["counters"], data["health"]
        if (
            not isinstance(checks_raw, dict)
            or not isinstance(outcomes, dict)
            or not isinstance(status_codes, dict)
            or len(checks_raw) > _MAX_CHECK_STATES
            or not isinstance(pending_raw, list)
            or len(pending_raw) > self.max_pending
            or not isinstance(timestamps, dict)
            or set(timestamps)
            != {
                "probe_started",
                "probe_completed",
                "evaluation",
                "delivery_attempt",
                "delivery_success",
                "resolution",
            }
            or not isinstance(counters, dict)
            or set(counters) != {"probe_attempts", "evaluations", "delivery_attempts"}
            or not isinstance(health, dict)
            or set(health) != {"probe", "delivery"}
        ):
            raise ValueError("invalid state shape")
        checks: dict[str, CheckState] = {}
        expected_check_fields = {
            "consecutive_failures",
            "consecutive_successes",
            "firing",
            "notified_firing",
            "transition_sequence",
            "last_attempt_at",
            "last_evaluation_at",
            "last_resolution_at",
        }
        for name, raw_check in checks_raw.items():
            if (
                not isinstance(name, str)
                or not _CHECK_NAME.fullmatch(name)
                or not isinstance(raw_check, dict)
            ):
                raise ValueError("invalid check")
            if set(raw_check) != expected_check_fields:
                raise ValueError("invalid check")
            check = CheckState(**raw_check)
            checks[name] = check
        pending: list[PendingNotification] = []
        for raw_event in pending_raw:
            if not isinstance(raw_event, dict) or set(raw_event) != {
                "event_id",
                "check_name",
                "firing",
                "occurred_at",
                "attempts",
                "next_attempt_at",
            }:
                raise ValueError("invalid event")
            pending.append(PendingNotification(**raw_event))
        timestamp_values = list(timestamps.values())
        counter_values = list(counters.values())
        if (
            not all(_validate_timestamp(value) for value in timestamp_values)
            or not all(_check_counter(value) for value in counter_values)
            or health["probe"] not in _HEALTH
            or health["delivery"] not in _HEALTH
        ):
            raise ValueError("invalid metadata")
        state = MonitorState(
            checks=checks,
            outcomes=outcomes,
            status_codes=status_codes,
            pending=tuple(pending),
            last_probe_started_at=timestamps["probe_started"],
            last_probe_completed_at=timestamps["probe_completed"],
            last_evaluation_at=timestamps["evaluation"],
            last_delivery_attempt_at=timestamps["delivery_attempt"],
            last_delivery_success_at=timestamps["delivery_success"],
            last_resolution_at=timestamps["resolution"],
            probe_attempts_total=counters["probe_attempts"],
            evaluations_total=counters["evaluations"],
            delivery_attempts_total=counters["delivery_attempts"],
            last_delivery_outcome=data["last_delivery_outcome"],
            probe_health=health["probe"],
            delivery_health=health["delivery"],
        )
        self._encode(state)
        return state
