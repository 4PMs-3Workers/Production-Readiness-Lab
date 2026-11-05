import json
from types import SimpleNamespace

import pytest
from tools.deploy import rollout_readiness
from tools.deploy.rollout_readiness import ready
from tools.deploy.rollout_state import RolloutError


def log(scans, worker="a" * 32):
    return json.dumps(
        dict(
            event="heartbeat",
            worker_id=worker,
            phase="running",
            database_ok=True,
            successful_scans=scans,
            last_successful_scan="2020-01-01T00:00:00Z",
        )
    ).encode()


class Budget:
    def remaining(self, cap: float = 600) -> float:
        return cap


class Runtime:
    origin = "https://example.invalid"

    def __init__(self, outputs):
        self.outputs = outputs
        self.deadline = Budget()
        self.verified = 0

    def verify_images(self):
        self.verified += 1

    def inspect(self, service):
        return dict(id="b" * 64, running=True, health="healthy")

    def command(self, *args, **kwargs):
        return self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(
        rollout_readiness, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    )
    monkeypatch.setattr(rollout_readiness, "gateway_ready", lambda *args: True)
    return now


def test_two_advancing_scans_prove_progress_despite_database_clock_skew(clock):
    host = Runtime([log(3), log(4)])
    ready(host)
    assert host.verified == 2


def test_repeated_heartbeats_do_not_prove_worker_progress(clock):
    host = Runtime([log(3)])
    with pytest.raises(RolloutError, match="readiness-failed"):
        ready(host)
    assert clock[0] == 90


def test_different_worker_generations_cannot_combine_their_counters(clock):
    host = Runtime([log(3), log(4, "b" * 32)])
    with pytest.raises(RolloutError, match="readiness-failed"):
        ready(host)


def test_unhealthy_gateway_blocks_otherwise_advancing_worker(clock, monkeypatch):
    host = Runtime([log(3), log(4)])
    monkeypatch.setattr(rollout_readiness, "gateway_ready", lambda *args: False)
    with pytest.raises(RolloutError, match="readiness-failed"):
        ready(host)
