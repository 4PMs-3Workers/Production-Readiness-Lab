"""Digest-only Compose execution; database volumes and migration history are retained."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from tools.deploy.release import render
from tools.deploy.release_manifest import MAX_JSON, Manifest, load_json
from tools.deploy.rollout_state import Reason, RolloutError


class Deadline:
    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.started = clock()
        self.expires = self.started + seconds

    def remaining(self, cap: float = 600) -> float:
        remaining = self.expires - self.clock()
        if remaining <= 0:
            raise RolloutError("deadline-exceeded")
        return min(cap, remaining)

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)


class ReleaseRuntime:
    def __init__(
        self,
        base: list[str],
        environment: dict[str, str],
        origin: str,
        project: str,
        scratch: Path,
        *,
        overlay: str = "local",
        observability: bool = False,
    ):
        self.base, self.environment = list(base), dict(environment)
        self.origin, self.project, self.scratch = origin, project, scratch
        self.overlay, self.observability = overlay, observability
        self.deadline = Deadline(600)
        self.manifest: Manifest | None = None
        self.overlay_path: Path | None = None

    def command(self, args: list[str], *, reason: Reason = "rollout-failed", cap=180) -> bytes:
        try:
            result = subprocess.run(
                args,
                env=self.environment,
                capture_output=True,
                check=False,
                timeout=self.deadline.remaining(cap),
            )
            self.deadline.remaining()
            if result.returncode:
                raise RolloutError(reason)
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            self.deadline.remaining()
            raise RolloutError(reason) from None

    def compose(self, *args: str, reason: Reason = "rollout-failed", cap=180) -> bytes:
        if self.overlay_path is None:
            raise RolloutError("invalid-manifest")
        return self.command(
            self.base + ["-f", str(self.overlay_path), *args], reason=reason, cap=cap
        )

    def select(self, manifest: Manifest) -> None:
        if (manifest.configuration.overlay, manifest.configuration.observability) != (
            self.overlay,
            self.observability,
        ):
            raise RolloutError("incompatible-configuration")
        raw = render(manifest)
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.scratch / (hashlib.sha256(raw).hexdigest() + ".yaml")
        if path.is_symlink() or (path.exists() and path.read_bytes() != raw):
            raise RolloutError("invalid-state")
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        self.manifest, self.overlay_path = manifest, path
        self.environment["SERVICE_RELEASE"] = manifest.provenance.source_sha

    def validate_plan(self) -> dict:
        if self.manifest is None:
            raise RolloutError("invalid-manifest")
        raw = self.compose(
            "--profile",
            "operations",
            "--profile",
            "seed",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
            reason="incompatible-configuration",
        )
        try:
            config = load_json(raw)
            services = config["services"]
            if not isinstance(services, dict) or any(
                value.get("build") for value in services.values()
            ):
                raise ValueError()
            for name, key in self.manifest.services.items():
                if name == "backup":
                    continue
                if services[name]["image"] != self.manifest.images[key].reference:
                    raise ValueError()
            for name, image in self.manifest.vendor_images.items():
                if services[name]["image"] != image:
                    raise ValueError()
            if self.observability and services["backup_metrics_exporter"]["image"] != (
                self.manifest.images["backend"].reference
            ):
                raise ValueError()
            return config
        except (ValueError, KeyError, TypeError):
            raise RolloutError("incompatible-configuration") from None

    def migrate(self) -> None:
        # Native app.deploy preserves the migration/backup named lifecycle guard.
        self.compose(
            "--profile",
            "operations",
            "run",
            "--pull",
            "never",
            "--no-deps",
            "--rm",
            "-T",
            "migrate",
            reason="migration-failed",
            cap=125,
        )

    def rollout(self) -> None:
        self.compose("stop", "--timeout", "40", "api", "worker", "frontend", cap=140)
        self.compose("run", "--pull", "never", "--no-deps", "--rm", "-T", "schema", cap=15)
        self.compose(
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            "api",
            "worker",
            "frontend",
            cap=100,
        )
        # Recreate the reviewed gateway to refresh service DNS, without recreating MySQL.
        self.compose(
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            "gateway",
            cap=40,
        )

    def json_output(self, raw: bytes) -> dict:
        try:
            if len(raw) > MAX_JSON:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, TypeError, UnicodeError):
            raise RolloutError("invalid-state") from None
