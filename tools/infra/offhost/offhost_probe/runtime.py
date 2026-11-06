from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .config import CheckConfig, MonitorConfig
from .evaluator import CheckState, ProbeResult, evaluate
from .notify import DeliveryResult, NotificationEvent, WebhookNotifier
from .probe import HttpsTransport, ProbePolicy
from .state import MonitorState, PendingNotification, StateStore


class Transport(Protocol):
    async def probe(self, check: CheckConfig, policy: ProbePolicy) -> ProbeResult: ...


class Notifier(Protocol):
    async def deliver(self, event: NotificationEvent) -> DeliveryResult: ...


@dataclass(frozen=True)
class RunSummary:
    completed_at: str
    results: tuple[ProbeResult, ...]
    transitions: int
    state: MonitorState
    capacity_limited: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MonitorRunner:
    def __init__(
        self,
        config: MonitorConfig,
        *,
        transport: Transport,
        notifier: Notifier,
        store: StateStore,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_summary: Callable[[RunSummary], None] | None = None,
        on_delivery: Callable[[NotificationEvent, DeliveryResult, str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.notifier = notifier
        self.store = store
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.on_summary = on_summary
        self.on_delivery = on_delivery
        persisted = store.load()
        self.configured_names = {check.name for check in config.checks}
        checks = {}
        last_resolution_at = persisted.last_resolution_at
        for name, check in persisted.checks.items():
            if name in self.configured_names:
                checks[name] = check
            elif check.firing:
                retired_at = self.clock()
                if retired_at.tzinfo is None or retired_at.utcoffset() is None:
                    raise ValueError("Monitor clock must be timezone-aware")
                retired_at_text = retired_at.isoformat(timespec="milliseconds")
                checks[name] = replace(
                    check,
                    firing=False,
                    notified_firing=True,
                    transition_sequence=check.transition_sequence + 1,
                    last_evaluation_at=retired_at_text,
                    last_resolution_at=retired_at_text,
                )
                last_resolution_at = retired_at_text
            elif check.notified_firing:
                checks[name] = check
        for pending in persisted.pending:
            if (
                pending.check_name not in self.configured_names
                and pending.firing
                and pending.check_name not in checks
            ):
                checks[pending.check_name] = CheckState(
                    notified_firing=True,
                    last_evaluation_at=pending.occurred_at,
                )
        self.state = replace(
            persisted,
            checks=checks,
            last_resolution_at=last_resolution_at,
            outcomes={
                name: value
                for name, value in persisted.outcomes.items()
                if name in self.configured_names
            },
            status_codes={
                name: value
                for name, value in persisted.status_codes.items()
                if name in self.configured_names
            },
        )
        self.capacity_limited = self._would_exceed_state_capacity()

    def _would_exceed_state_capacity(self) -> bool:
        return len(set(self.state.checks) | self.configured_names) > self.store.max_check_states

    async def run_once(self) -> RunSummary:
        notifications_processed = False
        if self.capacity_limited:
            self.state = await self._process_notifications(self.state)
            notifications_processed = True
            self.capacity_limited = self._would_exceed_state_capacity()
            if self.capacity_limited:
                return self._capacity_limited_summary()
        if self.config.max_pending_notifications - len(self.state.pending) < len(
            self.config.checks
        ):
            if not notifications_processed:
                self.state = await self._process_notifications(self.state)
                notifications_processed = True
            if self.config.max_pending_notifications - len(self.state.pending) < len(
                self.config.checks
            ):
                return self._capacity_limited_summary()
        started = self.clock()
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError("Monitor clock must be timezone-aware")
        initial = replace(
            self.state,
            last_probe_started_at=started.isoformat(timespec="milliseconds"),
            probe_attempts_total=self.state.probe_attempts_total + 1,
        )
        self.store.save(initial)
        policy = ProbePolicy(
            self.config.timeout_seconds,
            self.config.connect_timeout_seconds,
            self.config.read_timeout_seconds,
        )
        results = tuple(
            await asyncio.gather(
                *(self.transport.probe(check, policy) for check in self.config.checks)
            )
        )
        completed = self.clock()
        transitions, check_states = evaluate(
            results,
            self.state.checks,
            failure_threshold=self.config.failure_threshold,
            recovery_threshold=self.config.recovery_threshold,
            now=completed,
            attempted_at=started,
        )
        state = replace(
            initial,
            checks=check_states,
            outcomes={result.check_name: result.outcome for result in results},
            status_codes={result.check_name: result.status_code for result in results},
            pending=self.state.pending,
            last_probe_completed_at=completed.isoformat(timespec="milliseconds"),
            last_evaluation_at=completed.isoformat(timespec="milliseconds"),
            evaluations_total=self.state.evaluations_total + 1,
            last_resolution_at=next(
                (
                    transition.occurred_at
                    for transition in reversed(transitions)
                    if not transition.firing
                ),
                self.state.last_resolution_at,
            ),
            probe_health="healthy" if all(result.success for result in results) else "degraded",
            delivery_health=self.state.delivery_health,
        )
        state = replace(
            state,
            pending=state.pending
            + tuple(
                PendingNotification(
                    transition.event_id,
                    transition.check_name,
                    transition.firing,
                    transition.occurred_at,
                )
                for transition in transitions
            ),
        )
        state = self._queue_notifications(state)
        if notifications_processed and state.pending:
            state = replace(state, delivery_health="degraded")
        self.state = state
        self.store.save(state)
        if not notifications_processed:
            self.state = await self._process_notifications(self.state)
        summary = RunSummary(
            self.state.last_probe_completed_at or completed.isoformat(timespec="milliseconds"),
            results,
            len(transitions),
            self.state,
        )
        if self.on_summary is not None:
            self.on_summary(summary)
        return summary

    def _capacity_limited_summary(self) -> RunSummary:
        self.state = replace(
            self.state,
            probe_health="degraded",
            delivery_health="degraded",
        )
        self.store.save(self.state)
        now = self.clock()
        summary = RunSummary(
            now.isoformat(timespec="milliseconds"),
            (),
            0,
            self.state,
            capacity_limited=True,
        )
        if self.on_summary is not None:
            self.on_summary(summary)
        return summary

    async def _process_notifications(self, state: MonitorState) -> MonitorState:
        self.state = state
        try:
            async with asyncio.timeout(self.config.delivery_cycle_timeout_seconds):
                for _ in range(9):
                    queued = self._queue_notifications(self.state)
                    if queued != self.state:
                        self.store.save(queued)
                        self.state = queued
                    if not self.state.pending:
                        break
                    self.state = await self._deliver_pending(self.state)
                    if self.state.pending:
                        break
        except TimeoutError:
            self.state = replace(
                self.state,
                delivery_health="degraded",
                last_delivery_outcome="timeout",
            )
            self.store.save(self.state)
        return self.state

    def _queue_notifications(self, state: MonitorState) -> MonitorState:
        pending = list(state.pending)
        latest_pending_state = {event.check_name: event.firing for event in pending}
        queue_full = False
        for name, check in sorted(state.checks.items()):
            expected_state = latest_pending_state.get(name, check.notified_firing)
            if check.firing == expected_state:
                continue
            if len(pending) >= self.config.max_pending_notifications:
                queue_full = True
                continue
            occurred_at = check.last_evaluation_at or self.clock().isoformat(
                timespec="milliseconds"
            )
            event_id = hashlib.sha256(
                f"{name}:{check.transition_sequence}:{int(check.firing)}:{occurred_at}".encode()
            ).hexdigest()
            pending.append(PendingNotification(event_id, name, check.firing, occurred_at))
            latest_pending_state[name] = check.firing
        return replace(
            state,
            pending=tuple(pending),
            delivery_health="degraded" if queue_full else state.delivery_health,
        )

    async def _deliver_pending(self, state: MonitorState) -> MonitorState:
        pending = list(state.pending)
        current = state
        while pending:
            event = pending[0]
            if event.next_attempt_at:
                due_at = datetime.fromisoformat(event.next_attempt_at.replace("Z", "+00:00"))
                if self.clock() < due_at:
                    break
            delivered = False
            result_outcome = "network_error"
            completed_at: str | None = None
            for attempt_index in range(self.config.delivery_retries + 1):
                now = self.clock()
                current = replace(
                    current,
                    last_delivery_attempt_at=now.isoformat(timespec="milliseconds"),
                    delivery_attempts_total=current.delivery_attempts_total + 1,
                )
                self.store.save(current)
                self.state = current
                pending[0] = replace(pending[0], attempts=pending[0].attempts + 1)
                current = replace(current, pending=tuple(pending))
                self.store.save(current)
                self.state = current
                result = await self.notifier.deliver(
                    NotificationEvent(
                        event.event_id, event.check_name, event.firing, event.occurred_at
                    )
                )
                completed_at = self.clock().isoformat(timespec="milliseconds")
                if self.on_delivery is not None:
                    self.on_delivery(
                        NotificationEvent(
                            event.event_id, event.check_name, event.firing, event.occurred_at
                        ),
                        result,
                        now.isoformat(timespec="milliseconds"),
                        completed_at,
                    )
                result_outcome = result.outcome
                if result.delivered:
                    delivered = True
                    break
                if attempt_index < self.config.delivery_retries:
                    await self.sleep(min(0.25 * (2**attempt_index), 1.0))
            if delivered:
                completed_at = completed_at or self.clock().isoformat(timespec="milliseconds")
                pending.pop(0)
                checks = dict(current.checks)
                if event.check_name in checks:
                    if not event.firing and event.check_name not in self.configured_names:
                        checks.pop(event.check_name)
                    else:
                        checks[event.check_name] = replace(
                            checks[event.check_name], notified_firing=event.firing
                        )
                current = replace(
                    current,
                    checks=checks,
                    pending=tuple(pending),
                    last_delivery_success_at=completed_at,
                    last_delivery_outcome="accepted",
                    delivery_health="healthy" if not pending else "degraded",
                )
                self.store.save(current)
                self.state = current
            else:
                pending[0] = replace(
                    pending[0],
                    next_attempt_at=(
                        self.clock() + timedelta(seconds=self.config.interval_seconds)
                    ).isoformat(timespec="milliseconds"),
                )
                current = replace(
                    current,
                    pending=tuple(pending),
                    last_delivery_outcome=result_outcome,
                    delivery_health="degraded",
                )
                self.store.save(current)
                self.state = current
                break
        return current

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            cycle_started = self.monotonic()
            await self.run_once()
            delay = max(0.0, self.config.interval_seconds - (self.monotonic() - cycle_started))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue


def build_runner(config: MonitorConfig) -> MonitorRunner:
    import ssl

    try:
        context = ssl.create_default_context()
        if config.ca_bundle_path is not None:
            context.load_verify_locations(cafile=str(config.ca_bundle_path))
    except (OSError, ssl.SSLError):
        raise ValueError("Monitor configuration is invalid") from None
    transport = HttpsTransport(ssl_context=context)
    notifier = WebhookNotifier.from_environment(
        config.notification_url,
        timeout_seconds=config.delivery_timeout_seconds,
        token_env=config.token_env,
    )
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    return MonitorRunner(config, transport=transport, notifier=notifier, store=store)
