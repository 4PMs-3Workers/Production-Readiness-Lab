from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from offhost_probe.config import MonitorConfig, load_config
from offhost_probe.evaluator import CheckState, ProbeResult, evaluate


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
