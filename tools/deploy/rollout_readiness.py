"""Readiness requires live business edges and advancing work, never heartbeat alone."""

from __future__ import annotations

import ssl
import time
from typing import Protocol
from urllib.error import URLError
from urllib.request import urlopen

from tools.deploy.rollout_state import Reason, RolloutError
from tools.deploy.worker_status import WorkerProgressTracker, latest_worker_progress


class ReadinessBudget(Protocol):
    def remaining(self, cap: float = 600) -> float: ...


class ReadinessRuntime(Protocol):
    @property
    def origin(self) -> str: ...

    @property
    def deadline(self) -> ReadinessBudget: ...

    def verify_images(self) -> None: ...

    def inspect(self, service: str) -> dict | None: ...

    def command(self, args: list[str], *, reason: Reason, cap: float) -> bytes: ...


def gateway_ready(runtime: ReadinessRuntime, context: ssl.SSLContext | None = None) -> bool:
    try:
        for path in ("/health/gateway", "/health/ready", "/login"):
            with urlopen(
                runtime.origin.rstrip("/") + path,
                timeout=runtime.deadline.remaining(5),
                context=context,
            ) as response:
                if response.status != 200:
                    return False
        return True
    except (OSError, URLError, ValueError):
        return False


def ready(runtime: ReadinessRuntime, context: ssl.SSLContext | None = None) -> None:
    expires = time.monotonic() + runtime.deadline.remaining(90)
    tracker = WorkerProgressTracker()
    worker_container = None
    while time.monotonic() < expires:
        runtime.deadline.remaining()
        runtime.verify_images()
        healthy = all(
            (runtime.inspect(service) or {}).get("health") == "healthy"
            for service in ("api", "frontend", "gateway")
        )
        worker = runtime.inspect("worker")
        if worker is None or worker.get("running") is not True:
            tracker.observe(None, running=False)
        else:
            identifier = worker["id"]
            if worker_container is not None and identifier != worker_container:
                raise RolloutError("readiness-failed")
            worker_container = identifier
            output = runtime.command(
                ["docker", "logs", "--tail", "200", identifier], reason="readiness-failed", cap=5
            ).decode("utf-8")
            progress = latest_worker_progress(output, require_heartbeat=False)
            advanced = tracker.observe(progress, running=True)
            if advanced and healthy and gateway_ready(runtime, context):
                runtime.deadline.remaining()
                return
        remaining = min(expires - time.monotonic(), runtime.deadline.remaining())
        if remaining > 0:
            time.sleep(min(0.5, remaining))
    runtime.deadline.remaining()
    raise RolloutError("readiness-failed")
