"""Allowlisted JSON diagnostics independent of business transaction success."""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.worker.models import CandidateResult, FailureReason, WorkerState

Event = Literal[
    "starting", "ready", "heartbeat", "scan", "candidate", "stopping", "stopped", "fatal"
]


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return value.hex
    raise TypeError("Unsupported diagnostic value")


class Reporter:
    def __init__(self, sink: Callable[[str], object] | None = None) -> None:
        self._sink = sink if sink is not None else logging.getLogger("app.worker").info

    def emit(
        self,
        event: Event,
        state: WorkerState,
        *,
        reason: FailureReason | None = None,
        result: CandidateResult | None = None,
    ) -> bool:
        try:
            payload = {"event": event, **asdict(state)}
            if reason is not None:
                payload["reason"] = reason.value
            if result is not None:
                payload["candidate"] = asdict(result)
            self._sink(json.dumps(payload, default=_encode, allow_nan=False, sort_keys=True))
            return True
        except Exception:
            # Never propagate a logging/serialization failure into a committed write.
            state.reporting_failures += 1
            return False
