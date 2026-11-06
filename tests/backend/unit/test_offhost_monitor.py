from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from offhost_probe import cli as monitor_cli
from offhost_probe import notify as notify_module
from offhost_probe.config import MonitorConfig, load_config
from offhost_probe.evaluator import CheckState, ProbeResult, evaluate
from offhost_probe.notify import DeliveryResult, NotificationEvent, WebhookNotifier
from offhost_probe.runtime import MonitorRunner
from offhost_probe.state import MonitorState, PendingNotification, StateStore


def config_file(tmp_path, *, extra: str = ""):
    path = tmp_path / "monitor.toml"
    path.write_text(
        """[monitor]
interval_seconds = 30
timeout_seconds = 10
connect_timeout_seconds = 4
read_timeout_seconds = 4
failure_threshold = 3
recovery_threshold = 2
state_path = \"state.json\"

[notification]
webhook_url = \"http://127.0.0.1:8788/alerts\"

[[checks]]
name = \"gateway\"
url = \"https://example.test/health/ready\"
expected_statuses = [200]
max_response_bytes = 1024
""" + extra,
        encoding="utf-8",
    )
    return path


def test_load_config_accepts_bounded_https_check_and_loopback_receiver(tmp_path):
    config = load_config(config_file(tmp_path))

    assert isinstance(config, MonitorConfig)
    assert config.checks[0].name == "gateway"
    assert config.checks[0].url == "https://example.test/health/ready"
    assert config.state_path == tmp_path / "state.json"
    assert config.notification_url == "http://127.0.0.1:8788/alerts"


@pytest.mark.parametrize(
    "replacement",
    [
        'url = "http://example.test/health"',
        'url = "https://user:password@example.test/health"',
        'url = "https://example.test/health?token=secret"',
        'url = "https://example.test/health/å"',
        "max_response_bytes = 999999999",
        "expected_statuses = []",
        'name = "gateway/private"',
    ],
)
def test_load_config_rejects_unsafe_or_unbounded_checks(tmp_path, replacement):
    text = config_file(tmp_path).read_text(encoding="utf-8")
    key = replacement.split(" = ", 1)[0]
    original = next(line for line in text.splitlines() if line.startswith(key + " = "))
    path = tmp_path / "monitor.toml"
    path.write_text(text.replace(original, replacement), encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_oversized_configuration(tmp_path):
    path = tmp_path / "monitor.toml"
    path.write_bytes(b"#" * 65537)

    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(("failure_threshold", "valid"), [(1, True), (2, False), (10, False)])
def test_load_config_bounds_detection_latency_by_serial_probe_cycles(
    tmp_path, failure_threshold, valid
):
    path = config_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("interval_seconds = 30", "interval_seconds = 5")
    text = text.replace("timeout_seconds = 10", "timeout_seconds = 30")
    text = text.replace("connect_timeout_seconds = 4", "connect_timeout_seconds = 15")
    text = text.replace("read_timeout_seconds = 4", "read_timeout_seconds = 15")
    text = text.replace("failure_threshold = 3", f"failure_threshold = {failure_threshold}")
    text = text.replace(
        'state_path = "state.json"',
        'state_path = "state.json"\ndelivery_cycle_timeout_seconds = 60',
    )
    path.write_text(text, encoding="utf-8")

    if valid:
        assert load_config(path).failure_threshold == failure_threshold
    else:
        with pytest.raises(ValueError, match="configuration"):
            load_config(path)


def test_load_config_includes_wait_for_first_probe_in_detection_bound(tmp_path):
    path = config_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("interval_seconds = 30", "interval_seconds = 300")
    text = text.replace("failure_threshold = 3", "failure_threshold = 1")
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="configuration"):
        load_config(path)


def test_load_config_requires_outbox_capacity_for_each_configured_check(tmp_path):
    path = config_file(
        tmp_path,
        extra="""
[[checks]]
name = "worker"
url = "https://example.test/worker/ready"
expected_statuses = [200]
max_response_bytes = 1024
""",
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"',
            'state_path = "state.json"\nmax_pending_notifications = 1',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="configuration"):
        load_config(path)


def test_build_runner_adds_custom_ca_without_replacing_system_trust(tmp_path, monkeypatch):
    ca_path = tmp_path / "private-ca.pem"
    ca_path.write_text("test certificate bundle", encoding="utf-8")
    config_path = config_file(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"',
            f'state_path = "state.json"\nca_bundle_path = "{ca_path.as_posix()}"',
        ),
        encoding="utf-8",
    )
    loaded = load_config(config_path)
    calls = []

    class FakeSSLContext:
        def load_verify_locations(self, *, cafile):
            calls.append(cafile)

    def create_default_context(**kwargs):
        assert kwargs == {}
        return FakeSSLContext()

    monkeypatch.setattr(ssl, "create_default_context", create_default_context)

    from offhost_probe.runtime import build_runner

    build_runner(loaded)

    assert calls == [str(ca_path)]


def test_status_reports_stale_success_as_degraded(tmp_path, capsys):
    config_path = config_file(tmp_path)
    config = load_config(config_path)
    stale_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="milliseconds")
    StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    ).save(
        MonitorState(
            checks={
                "gateway": CheckState(last_attempt_at=stale_time, last_evaluation_at=stale_time)
            },
            last_probe_started_at=stale_time,
            last_probe_completed_at=stale_time,
            last_evaluation_at=stale_time,
            probe_health="healthy",
            delivery_health="healthy",
        )
    )

    assert monitor_cli._status(config_path) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["monitor_health"] == "degraded"
    assert report["probe_health"] == "degraded"
    assert report["probe_stale"] is True


def test_runner_resolves_removed_firing_checks_before_pruning_state(tmp_path):
    config = load_config(config_file(tmp_path))
    old_checks = {
        f"old{index}": CheckState(firing=True, notified_firing=True, transition_sequence=1)
        for index in range(8)
    }
    StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    ).save(MonitorState(checks=old_checks))
    notifier = FakeNotifier(True)
    runner = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=notifier,
        store=StateStore(
            config.state_path,
            max_bytes=config.max_state_bytes,
            max_pending=config.max_pending_notifications,
        ),
        clock=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )

    summary = asyncio.run(runner.run_once())

    assert set(summary.state.checks) == {"gateway"}
    assert {event.check_name for event in notifier.events} == set(old_checks)
    assert all(not event.firing for event in notifier.events)


def test_runner_bounds_retired_check_states_when_notification_queue_is_full(tmp_path):
    extra_checks = "\n".join(
        f'[[checks]]\nname = "new{index}"\nurl = "https://example.test/{index}"\n'
        "expected_statuses = [200]\nmax_response_bytes = 1024"
        for index in range(7)
    )
    path = config_file(tmp_path, extra=extra_checks)
    text = path.read_text(encoding="utf-8").replace(
        'state_path = "state.json"',
        'state_path = "state.json"\nmax_pending_notifications = 64\ndelivery_retries = 0',
    )
    path.write_text(text, encoding="utf-8")
    config = load_config(path)
    timestamp = "2026-11-04T00:00:00+00:00"
    pending = tuple(
        PendingNotification(f"{index:064x}", f"pending{index}", True, timestamp)
        for index in range(64)
    )
    retired = {
        f"retired{index}": CheckState(firing=True, notified_firing=True, transition_sequence=1)
        for index in range(8)
    }
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    store.save(MonitorState(checks=retired, pending=pending))
    notifier = FakeNotifier(False)
    runner = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=notifier,
        store=store,
        clock=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )

    summary = asyncio.run(runner.run_once())

    assert len(summary.state.checks) == 72
    assert len(summary.state.pending) == 64
    assert summary.state.delivery_health == "degraded"
    assert summary.capacity_limited is True
    assert not ({check.name for check in config.checks} & set(summary.state.checks))


def test_load_config_rejects_state_budget_too_small_for_bounded_notification_queue(tmp_path):
    path = config_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("failure_threshold = 3", "failure_threshold = 1")
    text = text.replace("recovery_threshold = 2", "recovery_threshold = 1")
    text = text.replace(
        'state_path = "state.json"',
        'state_path = "state.json"\nmax_state_bytes = 4096\nmax_pending_notifications = 64',
    )
    additional_checks = "".join(
        f'\n[[checks]]\nname = "check{index}"\n'
        f'url = "https://check{index}.example.test/health"\n'
        "expected_statuses = [200]\nmax_response_bytes = 1024\n"
        for index in range(2, 9)
    )
    path.write_text(text + additional_checks, encoding="utf-8")
    with pytest.raises(ValueError, match="configuration"):
        load_config(path)
    assert not path.with_name("state.json").exists()


def test_load_config_accepts_full_notification_queue_with_default_state_budget(tmp_path):
    path = config_file(tmp_path)
    text = path.read_text(encoding="utf-8").replace(
        'state_path = "state.json"',
        'state_path = "state.json"\nmax_pending_notifications = 64',
    )
    path.write_text(text, encoding="utf-8")

    assert load_config(path).max_pending_notifications == 64


def test_runner_defers_new_checks_until_saturated_retired_states_can_drain(tmp_path):
    extra_checks = "\n".join(
        f'[[checks]]\nname = "new{index}"\nurl = "https://example.test/{index}"\n'
        "expected_statuses = [200]\nmax_response_bytes = 1024"
        for index in range(7)
    )
    path = config_file(tmp_path, extra=extra_checks)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"',
            'state_path = "state.json"\nmax_pending_notifications = 64\ndelivery_retries = 0',
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    timestamp = "2026-11-04T00:00:00+00:00"
    pending = tuple(
        PendingNotification(f"{index:064x}", f"pending{index}", True, timestamp)
        for index in range(64)
    )
    retired = {
        f"retired{index}": CheckState(firing=True, notified_firing=True, transition_sequence=1)
        for index in range(8)
    }
    previous = {
        f"previous{index}": CheckState(firing=True, notified_firing=True, transition_sequence=1)
        for index in range(8)
    }
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    store.save(MonitorState(checks=retired | previous, pending=pending))
    notifier = FakeNotifier(False)
    now = [datetime(2026, 11, 4, tzinfo=UTC)]
    runner = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=notifier,
        store=store,
        clock=lambda: now[0],
    )

    summary = asyncio.run(runner.run_once())

    assert summary.capacity_limited is True
    assert summary.results == ()
    assert summary.state.probe_health == "degraded"
    assert summary.state.probe_attempts_total == 0
    assert len(summary.state.checks) == 80
    assert len(summary.state.pending) == 64

    notifier.delivered = True
    for _ in range(20):
        now[0] += timedelta(minutes=1)
        summary = asyncio.run(runner.run_once())
        if not summary.capacity_limited:
            break

    assert summary.capacity_limited is False
    assert len(summary.results) == 8
    assert set(summary.state.checks) == {check.name for check in config.checks}


def test_evaluate_requires_nonempty_results():
    with pytest.raises(ValueError, match="[Aa]t least one"):
        evaluate((), {}, failure_threshold=3, recovery_threshold=2, now=datetime.now(UTC))


def test_evaluate_fires_after_three_successive_failures_and_resolves_after_two_successes():
    now = datetime(2026, 11, 4, 0, 0, tzinfo=UTC)
    state: dict[str, CheckState] = {}

    def step(ok: bool, seconds: int):
        nonlocal state
        result = ProbeResult("gateway", ok, 200 if ok else None, "ok" if ok else "network_error")
        transition, state = evaluate(
            (result,),
            state,
            failure_threshold=3,
            recovery_threshold=2,
            now=now + timedelta(seconds=seconds),
        )
        return transition

    assert step(False, 0) == ()
    assert step(True, 30) == ()
    assert state["gateway"].consecutive_failures == 0
    assert step(False, 60) == ()
    firing = step(False, 90)
    assert firing == ()
    firing = step(False, 120)
    assert len(firing) == 1 and firing[0].firing is True
    assert state["gateway"].firing is True
    assert step(True, 150) == ()
    resolved = step(True, 180)
    assert len(resolved) == 1 and resolved[0].firing is False
    assert state["gateway"].firing is False


def test_evaluate_does_not_emit_duplicate_firing_transitions():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    state: dict[str, CheckState] = {}
    emitted = []
    for tick in range(3):
        events, state = evaluate(
            (ProbeResult("gateway", False, None, "timeout"),),
            state,
            failure_threshold=2,
            recovery_threshold=2,
            now=now + timedelta(seconds=tick),
        )
        emitted.extend(events)
    assert len(emitted) == 1
    assert emitted[0].firing is True


def test_state_store_atomically_persists_bounded_transition_and_timestamps(tmp_path):
    path = tmp_path / "state" / "monitor.json"
    store = StateStore(path, max_bytes=8192, max_pending=4)
    state = MonitorState(
        checks={"gateway": CheckState(firing=True, transition_sequence=1)},
        pending=(
            PendingNotification("a" * 64, "gateway", True, "2026-11-04T00:00:00+00:00", attempts=1),
        ),
        last_probe_started_at="2026-11-04T00:00:00+00:00",
        last_probe_completed_at="2026-11-04T00:00:01+00:00",
        last_evaluation_at="2026-11-04T00:00:01+00:00",
        probe_attempts_total=1,
        evaluations_total=1,
        probe_health="degraded",
    )

    store.save(state)

    loaded = StateStore(path, max_bytes=8192, max_pending=4).load()
    assert loaded == state
    assert path.stat().st_size <= 8192


@pytest.mark.parametrize("contents", ["{not json", "{}", " " * 9000])
def test_state_store_fails_closed_for_corrupt_oversized_or_unknown_state(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="state"):
        StateStore(path, max_bytes=8192, max_pending=4).load()


def test_state_store_rejects_unbounded_pending_notification_queue(tmp_path):
    store = StateStore(tmp_path / "state.json", max_bytes=8192, max_pending=1)
    state = MonitorState(
        pending=(
            PendingNotification("a" * 64, "gateway", True, "2026-11-04T00:00:00+00:00"),
            PendingNotification("b" * 64, "gateway", False, "2026-11-04T00:01:00+00:00"),
        )
    )

    with pytest.raises(ValueError, match="state"):
        store.save(state)


@pytest.mark.asyncio
async def test_webhook_deadline_aborts_transport_instead_of_waiting_for_tls_shutdown(monkeypatch):
    class HangingWriter:
        class Transport:
            aborted = False

            def abort(self):
                self.aborted = True

        transport = Transport()

        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            await asyncio.Event().wait()

    writer = HangingWriter()

    async def open_connection(*args, **kwargs):
        return asyncio.StreamReader(), writer

    monkeypatch.setattr(notify_module.asyncio, "open_connection", open_connection)
    notifier = WebhookNotifier("http://127.0.0.1/alerts", timeout_seconds=0.05)
    event = NotificationEvent("a" * 64, "gateway", True, "2026-11-05T00:00:00+00:00")

    result = await asyncio.wait_for(notifier.deliver(event), timeout=0.2)

    assert result == DeliveryResult(False, "timeout")
    assert writer.transport.aborted


@pytest.mark.asyncio
async def test_webhook_notifier_sends_stable_event_id_without_exposing_response_body():
    received: list[tuple[dict[str, str], bytes]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        header = await reader.readuntil(b"\r\n\r\n")
        headers = {
            line.split(b":", 1)[0].decode().lower(): line.split(b":", 1)[1].strip().decode()
            for line in header.split(b"\r\n")[1:]
            if b":" in line
        }
        body = await reader.readexactly(int(headers["content-length"]))
        received.append((headers, body))
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    event = NotificationEvent("f" * 64, "gateway", True, "2026-11-04T00:00:00+00:00")
    notifier = WebhookNotifier(f"http://127.0.0.1:{port}/alerts", timeout_seconds=2)
    try:
        first = await notifier.deliver(event)
        second = await notifier.deliver(event)
    finally:
        server.close()
        await server.wait_closed()

    assert first.delivered and second.delivered
    assert [item[0]["idempotency-key"] for item in received] == ["f" * 64, "f" * 64]
    assert b"gateway" in received[0][1]
    assert b"Authorization" not in received[0][1]


@pytest.mark.asyncio
async def test_webhook_notifier_reports_bounded_failure_without_response_details():
    async def reject(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 6\r\n\r\nsecret")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(reject, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    notifier = WebhookNotifier(f"http://127.0.0.1:{port}/alerts", timeout_seconds=2)
    try:
        result = await notifier.deliver(
            NotificationEvent("e" * 64, "gateway", False, "2026-11-04T00:00:00+00:00")
        )
    finally:
        server.close()
        await server.wait_closed()

    assert not result.delivered
    assert result.outcome == "rejected"


class FakeTransport:
    def __init__(self, success: bool):
        self.success = success

    async def probe(self, check, policy):
        return ProbeResult(
            check.name,
            self.success,
            200 if self.success else None,
            "ok" if self.success else "network_error",
        )


class FakeNotifier:
    def __init__(self, delivered: bool):
        self.delivered = delivered
        self.events: list[NotificationEvent] = []

    async def deliver(self, event: NotificationEvent) -> DeliveryResult:
        self.events.append(event)
        return DeliveryResult(self.delivered, "accepted" if self.delivered else "timeout")


def test_runner_persists_transition_outbox_before_crash_and_recovers_after_probe_improves(
    tmp_path,
):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("failure_threshold = 3", "failure_threshold = 1")
        .replace("recovery_threshold = 2", "recovery_threshold = 1")
        .replace(
            'state_path = "state.json"',
            'state_path = "state.json"\ndelivery_retries = 0',
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    backing_store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )

    class CrashAfterEvaluationStore(StateStore):
        def __init__(self):
            super().__init__(
                config.state_path,
                max_bytes=config.max_state_bytes,
                max_pending=config.max_pending_notifications,
            )
            self.saves = 0

        def load(self) -> MonitorState:
            return backing_store.load()

        def save(self, state: MonitorState) -> None:
            self.saves += 1
            backing_store.save(state)
            if self.saves == 2:
                raise RuntimeError("simulated crash after evaluation persistence")

    crashed = MonitorRunner(
        config,
        transport=FakeTransport(False),
        notifier=FakeNotifier(True),
        store=CrashAfterEvaluationStore(),
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(crashed.run_once())

    durable = backing_store.load()
    assert durable.checks["gateway"].firing is True
    assert len(durable.pending) == 1
    firing_id = durable.pending[0].event_id

    recovered_notifier = FakeNotifier(True)
    recovered = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=recovered_notifier,
        store=backing_store,
        clock=lambda: datetime(2026, 11, 5, 0, 1, tzinfo=UTC),
    )
    asyncio.run(recovered.run_once())

    assert recovered_notifier.events[0].event_id == firing_id
    assert recovered_notifier.events[0].firing is True
    assert [event.firing for event in recovered_notifier.events] == [True, False]


def test_runner_preserves_repeated_transitions_for_one_check_across_restart(tmp_path):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("failure_threshold = 3", "failure_threshold = 1")
        .replace("recovery_threshold = 2", "recovery_threshold = 1")
        .replace(
            'state_path = "state.json"',
            'state_path = "state.json"\ndelivery_retries = 0',
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    backing_store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    now = [datetime(2026, 11, 5, tzinfo=UTC)]
    notifier = FakeNotifier(True)
    runner = MonitorRunner(
        config,
        transport=FakeTransport(False),
        notifier=notifier,
        store=backing_store,
        clock=lambda: now[0],
    )
    asyncio.run(runner.run_once())

    notifier.delivered = False
    now[0] += timedelta(seconds=1)
    runner.transport = FakeTransport(True)
    asyncio.run(runner.run_once())
    now[0] += timedelta(seconds=1)
    runner.transport = FakeTransport(False)

    class CrashAfterEvaluationStore(StateStore):
        def __init__(self):
            super().__init__(
                config.state_path,
                max_bytes=config.max_state_bytes,
                max_pending=config.max_pending_notifications,
            )
            self.saves = 0

        def load(self) -> MonitorState:
            return backing_store.load()

        def save(self, state: MonitorState) -> None:
            self.saves += 1
            backing_store.save(state)
            if self.saves == 2:
                raise RuntimeError("simulated crash after transition persistence")

    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(
            MonitorRunner(
                config,
                transport=runner.transport,
                notifier=notifier,
                store=CrashAfterEvaluationStore(),
                clock=lambda: now[0],
            ).run_once()
        )

    durable = backing_store.load()
    assert [event.firing for event in durable.pending] == [False, True]
    assert durable.checks["gateway"].firing is True

    now[0] += timedelta(minutes=1)
    recovered_notifier = FakeNotifier(True)
    recovered = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=recovered_notifier,
        store=backing_store,
        clock=lambda: now[0],
    )
    summary = asyncio.run(recovered.run_once())

    assert summary.state.checks["gateway"].firing is False
    assert [event.firing for event in notifier.events] == [True, False]
    assert [event.firing for event in recovered_notifier.events] == [False, True, False]
    delivered = [notifier.events[0], *recovered_notifier.events]
    assert [event.firing for event in delivered] == [True, False, True, False]
    assert len({event.event_id for event in delivered}) == 4


def test_runner_applies_backpressure_when_outbox_cannot_hold_a_check_transition(tmp_path):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("failure_threshold = 3", "failure_threshold = 1")
        .replace(
            'state_path = "state.json"',
            'state_path = "state.json"\nmax_pending_notifications = 2',
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    pending = tuple(
        PendingNotification(
            event_id,
            "gateway",
            firing,
            "2026-11-05T00:00:00+00:00",
            next_attempt_at="2026-11-05T00:01:00+00:00",
        )
        for event_id, firing in (("a" * 64, True), ("b" * 64, False))
    )
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    store.save(MonitorState(pending=pending))

    class CountingTransport(FakeTransport):
        def __init__(self):
            super().__init__(False)
            self.calls = 0

        async def probe(self, check, policy):
            self.calls += 1
            return await super().probe(check, policy)

    transport = CountingTransport()
    now = [datetime(2026, 11, 5, tzinfo=UTC)]
    notifier = FakeNotifier(True)
    runner = MonitorRunner(
        config,
        transport=transport,
        notifier=notifier,
        store=store,
        clock=lambda: now[0],
    )

    summary = asyncio.run(runner.run_once())

    assert summary.capacity_limited is True
    assert summary.results == ()
    assert summary.state.probe_health == "degraded"
    assert summary.state.delivery_health == "degraded"
    assert summary.state.probe_attempts_total == 0
    assert summary.state.evaluations_total == 0
    assert summary.state.pending == pending
    assert transport.calls == 0

    now[0] += timedelta(minutes=2)
    resumed = asyncio.run(runner.run_once())

    assert resumed.capacity_limited is False
    assert len(resumed.results) == 1
    assert [event.firing for event in resumed.state.pending] == [True]
    assert resumed.state.delivery_health == "degraded"
    assert [event.firing for event in notifier.events] == [True, False]
    assert (transport.calls, resumed.state.delivery_attempts_total) == (1, 2)


@pytest.mark.asyncio
async def test_cycle_timeout_keeps_earlier_delivery_acknowledgement_and_attempts(tmp_path):
    path = config_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"',
            'state_path = "state.json"\n'
            "delivery_cycle_timeout_seconds = 1\n"
            "delivery_retries = 0",
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    first = PendingNotification("a" * 64, "gateway", True, "2026-11-05T00:00:00+00:00")
    second = PendingNotification("b" * 64, "gateway", False, "2026-11-05T00:01:00+00:00")
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    store.save(MonitorState(pending=(first, second)))

    class SlowNotifier:
        async def deliver(self, event):
            await asyncio.sleep(0.6)
            return DeliveryResult(True, "accepted")

    runner = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=SlowNotifier(),
        store=store,
    )
    result = await runner._process_notifications(runner.state)
    durable = store.load()

    assert [event.event_id for event in durable.pending] == [second.event_id]
    assert durable.pending[0].attempts == 1
    assert durable.delivery_attempts_total == 2
    assert durable.last_delivery_outcome == "timeout"
    assert result == durable


def test_runner_recovers_pending_firing_delivery_after_restart_and_resolves(tmp_path):
    path = config_file(tmp_path)
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace("failure_threshold = 3", "failure_threshold = 1")
        .replace("recovery_threshold = 2", "recovery_threshold = 2")
        .replace("delivery_retries = 2", "delivery_retries = 0"),
        encoding="utf-8",
    )
    config = load_config(path)
    store = StateStore(
        config.state_path,
        max_bytes=config.max_state_bytes,
        max_pending=config.max_pending_notifications,
    )
    now = datetime(2026, 11, 4, tzinfo=UTC)
    failed_notifier = FakeNotifier(False)
    first = MonitorRunner(
        config,
        transport=FakeTransport(False),
        notifier=failed_notifier,
        store=store,
        clock=lambda: now,
    )

    firing = asyncio.run(first.run_once())
    assert firing.state.checks["gateway"].firing is True
    assert firing.state.delivery_health == "degraded"
    assert firing.state.pending[0].attempts == config.delivery_retries + 1
    pending_id = firing.state.pending[0].event_id
    assert len(failed_notifier.events) == config.delivery_retries + 1
    assert {event.event_id for event in failed_notifier.events} == {pending_id}

    recovered_notifier = FakeNotifier(True)
    restarted = MonitorRunner(
        config,
        transport=FakeTransport(True),
        notifier=recovered_notifier,
        store=StateStore(
            config.state_path,
            max_bytes=config.max_state_bytes,
            max_pending=config.max_pending_notifications,
        ),
        clock=lambda: now + timedelta(minutes=1),
    )
    first_recovery = asyncio.run(restarted.run_once())
    assert first_recovery.state.checks["gateway"].firing is True
    assert [event.event_id for event in recovered_notifier.events] == [pending_id]
    assert first_recovery.state.delivery_health == "healthy"

    resolved = asyncio.run(restarted.run_once())
    assert resolved.state.checks["gateway"].firing is False
    assert resolved.state.last_resolution_at is not None
    assert recovered_notifier.events[-1].firing is False
    assert len({event.event_id for event in recovered_notifier.events}) == 2
