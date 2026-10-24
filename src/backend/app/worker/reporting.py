"""Allowlisted JSON diagnostics independent of business transaction success."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.worker.models import CandidateResult, FailureReason, WorkerState
from app.worker.publishing import CLEANUP_TIMEOUT_SECONDS, Publisher

Event = Literal[
    "starting", "ready", "heartbeat", "scan", "candidate", "stopping", "stopped", "fatal"
]


class EventObserver(Protocol):
    """Observer hook shared by the worker telemetry owner and focused callers."""

    def observe(
        self,
        event: Event,
        state: WorkerState,
        *,
        reason: FailureReason | None = None,
        result: CandidateResult | None = None,
    ) -> None: ...


def _encode(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return value.hex
    raise TypeError("Unsupported diagnostic value")


class Reporter:
    def __init__(
        self,
        sink: Callable[[str], object] | None = None,
        *,
        observer: EventObserver | None = None,
    ) -> None:
        self._publisher = Publisher(sink)
        self._observed_failures = 0
        self._state: WorkerState | None = None
        self._observer = observer

    def _collect_failures(self, state: WorkerState) -> None:
        failures = self._publisher.failures
        state.reporting_failures += failures - self._observed_failures
        self._observed_failures = failures

    async def close(self, deadline: float | None = None) -> None:
        limit = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT_SECONDS
        try:
            await self._publisher.close(min(limit, deadline) if deadline is not None else limit)
        finally:
            if self._state is not None:
                self._collect_failures(self._state)

    async def __aenter__(self) -> "Reporter":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def emit(
        self,
        event: Event,
        state: WorkerState,
        *,
        reason: FailureReason | None = None,
        result: CandidateResult | None = None,
    ) -> bool:
        self._state = state
        self._collect_failures(state)
        if self._observer is not None:
            try:
                self._observer.observe(event, state, reason=reason, result=result)
            except Exception:
                # Observer failures are independent of stdout delivery and business work.
                pass
        try:
            payload = {"event": event, **asdict(state)}
            if reason is not None:
                payload["reason"] = reason.value
            if result is not None:
                payload["candidate"] = asdict(result)
            accepted = self._publisher.submit(
                json.dumps(payload, default=_encode, allow_nan=False, sort_keys=True)
            )
            self._collect_failures(state)
            return accepted
        except Exception:
            # Never propagate a logging/serialization failure into a committed write.
            state.reporting_failures += 1
            return False
