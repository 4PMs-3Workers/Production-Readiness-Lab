"""Bounded demo lifecycle. External env files are passed to Compose, never read here."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

# Support both direct execution and import-based operator contract tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
from worker_status import WorkerProgressTracker, latest_worker_progress  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REVISION_LABEL = "org.opencontainers.image.revision"
APPLICATION_SERVICES = {"api", "worker", "frontend", "migrate", "schema", "seed"}
ENV_FILES = (
    "PRL_DB_ENV_FILE",
    "PRL_RUNTIME_ENV_FILE",
    "PRL_MIGRATION_ENV_FILE",
    "PRL_SEED_ENV_FILE",
)


class OperationFailed(Exception):
    pass


class ProvenanceMismatch(OperationFailed):
    pass


def run(args, timeout=180):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise OperationFailed()
    return result.stdout.strip()


def worker_progress(output):
    return latest_worker_progress(output, require_heartbeat=True)


def worker_ready(output):
    return worker_progress(output) is not None


class Demo:
    def __init__(self, overlay, source_sha):
        self.overlay = overlay
        self.source_sha = source_sha
        self.base = [
            "docker",
            "compose",
            "--project-name",
            "prl-demo",
            "-f",
            str(ROOT / "tools/deploy/compose.yaml"),
            "-f",
            str(ROOT / f"tools/deploy/compose.{overlay}.yaml"),
        ]
        for name in ENV_FILES:
            value = os.environ.get(name, "")
            if not value or not Path(value).is_absolute() or not Path(value).is_file():
                raise OperationFailed()
        if overlay == "https":
            for name in ("PRL_TLS_CERT_FILE", "PRL_TLS_KEY_FILE"):
                if not Path(os.environ.get(name, "")).is_file():
                    raise OperationFailed()
            self.origin = os.environ.get("PRL_HTTPS_ORIGIN", "")
            parsed = urlsplit(self.origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise OperationFailed()
        else:
            port = os.environ.get("PRL_HTTP_PORT", "8080")
            if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise OperationFailed()
            self.origin = f"http://localhost:{port}"
        # Do not allow the caller to silently select a different project or file set.
        for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
            if os.environ.get(name):
                raise OperationFailed()

    def compose(self, *args, timeout=180):
        return run(self.base + list(args), timeout=timeout)

    def healthy(self, service):
        container = self.compose("ps", "-q", service)
        if not re.fullmatch(r"[0-9a-f]{12,64}", container):
            return False
        return (
            run(["docker", "inspect", "--format", "{{.State.Health.Status}}", container])
            == "healthy"
        )

    def wait(self, predicate):
        deadline = time.monotonic() + 120
        while True:
            try:
                if predicate():
                    return
            except (OperationFailed, OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise OperationFailed()
            time.sleep(2)

    def database(self):
        self.compose("up", "-d", "mysql")
        self.wait(lambda: self.healthy("mysql"))

    def verify(self):
        self.wait(lambda: all(self.healthy(service) for service in ("api", "frontend", "gateway")))

        def public_ready():
            for path in ("/health/gateway", "/health/ready", "/login"):
                with urlopen(self.origin.rstrip("/") + path, timeout=5) as response:
                    if response.status != 200:
                        return False
            return True

        self.wait(public_ready)

        tracker = WorkerProgressTracker()

        def worker():
            try:
                ids = self.compose("ps", "-q", "worker")
                if not re.fullmatch(r"[0-9a-f]{12,64}", ids):
                    tracker.observe(None, running=False)
                    return False
                running = run(["docker", "inspect", "--format", "{{.State.Running}}", ids])
                if running != "true":
                    tracker.observe(None, running=False)
                    return False
                logs = self.compose(
                    "logs", "--no-color", "--no-log-prefix", "--tail", "200", "worker"
                )
                progress = latest_worker_progress(logs, require_heartbeat=True)
            except (OperationFailed, OSError, ValueError):
                tracker.observe(None, running=False)
                return False
            return tracker.observe(progress, running=True)

        self.wait(worker)
        api = self.compose("images", "-q", "api")
        if not api or api != self.compose("images", "-q", "worker"):
            raise OperationFailed()

    def inspect_image(self, reference, service):
        raw = run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},'
                '"Labels":{{json (index .Config "Labels")}}}',
                reference,
            ]
        )
        image = json.loads(raw)
        if service in APPLICATION_SERVICES:
            if (image.get("Labels") or {}).get(REVISION_LABEL) != self.source_sha:
                raise ProvenanceMismatch()
        return image

    def require_images(self, services: Iterable[str]) -> None:
        services = tuple(services)
        profiles = [
            profile
            for service, profile in (("migrate", "operations"), ("seed", "seed"))
            if service in services
        ]
        profile_args = [argument for profile in profiles for argument in ("--profile", profile)]
        configuration = json.loads(
            self.compose(*profile_args, "config", "--no-env-resolution", "--format", "json")
        )
        for service in services:
            self.inspect_image(configuration["services"][service]["image"], service)

    def images(self, configured_only=False):
        images = {}
        configuration = json.loads(
            self.compose("config", "--no-env-resolution", "--format", "json")
        )
        for service in sorted(evidence.SERVICES):
            identifier = "" if configured_only else self.compose("images", "-q", service)
            if identifier and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{12,64}", identifier):
                raise OperationFailed()
            reference = identifier or configuration["services"][service]["image"]
            try:
                image = self.inspect_image(reference, service)
            except ProvenanceMismatch:
                raise
            except OperationFailed:
                if identifier:
                    raise
                continue
            images[service] = {"id": image["Id"], "digests": image.get("RepoDigests") or []}
        return images

    def execute(self, command):
        self.compose("config", "--quiet")
        if command == "build":
            self.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.source_sha}",
                "api",
                "frontend",
                timeout=900,
            )
            self.require_images(("api", "frontend"))
        elif command in {"migrate", "seed", "up"}:
            services = (
                {"schema", command}
                if command in {"migrate", "seed"}
                else {"schema", "api", "worker", "frontend"}
            )
            self.require_images(sorted(services))
            self.database()
            if command == "migrate":
                self.compose("run", "--rm", "migrate")
                self.compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh")
            self.compose("run", "--rm", "schema")
            if command == "seed":
                self.compose("run", "--rm", "seed")
            elif command == "up":
                # Existing schema/grants are checked above; normal up has no privileged jobs.
                self.compose("up", "-d", "api", "frontend", "gateway", "worker")
                self.verify()
        elif command == "status":
            output = self.compose("ps", "--all", "--format", "json")
            records = (
                json.loads(output)
                if output.lstrip().startswith("[")
                else [json.loads(line) for line in output.splitlines() if line.strip()]
            )
            if not records:
                print("Stack has no containers")
            for record in records:
                service = record.get("Service")
                state = record.get("State")
                health = record.get("Health")
                if service in evidence.SERVICES:
                    state = (
                        state
                        if state
                        in {
                            "running",
                            "exited",
                            "created",
                            "paused",
                            "restarting",
                            "dead",
                            "removing",
                        }
                        else "unknown"
                    )
                    health = health if health in {"healthy", "unhealthy", "starting"} else "unknown"
                    print(f"{service}: {state}, health={health}")
        elif command == "evidence":
            self.require_images(("schema",))
            self.images()
            self.compose("run", "--rm", "schema")
            self.verify()
        elif command == "stop":
            self.compose("stop")
        elif command == "down":
            self.compose("down")


def require_clean_source(expected_sha):
    # Capture only: filenames may themselves contain secrets.
    if run(["git", "status", "--porcelain", "--untracked-files=all"]):
        raise OperationFailed()
    if run(["git", "rev-parse", "HEAD"]) != expected_sha:
        raise OperationFailed()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (
        len(args) != 3
        or args[0] not in evidence.COMMANDS
        or args[1] != "--overlay"
        or args[2] not in {"local", "https"}
    ):
        print(
            "Usage: demo.sh {config|build|migrate|seed|up|stop|down|status|evidence} "
            "--overlay {local|https}",
            file=sys.stderr,
        )
        return 2
    command, _, overlay = args
    try:
        with evidence.lifecycle_lock(ROOT / "build/deploy/.lifecycle.lock"):
            sha = run(["git", "rev-parse", "HEAD"])
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise OperationFailed()
            require_clean_source(sha)
            path = ROOT / "build/deploy" / sha / "manifest.json"
            previous = evidence.validate(json.loads(path.read_text())) if path.exists() else None
            try:
                demo = Demo(overlay, sha)
                demo.execute(command)
                if command == "config":
                    images = {}
                else:
                    images = demo.images(configured_only=command == "build")
                require_clean_source(sha)
            except Exception:
                evidence.write(path, evidence.create(sha, overlay, command, "failed", {}, previous))
                raise
            evidence.write(path, evidence.create(sha, overlay, command, "passed", images, previous))
        print(f"Deployment {command} passed; evidence: build/deploy/{sha}/manifest.json")
        return 0
    except Exception:
        # Compose/driver/TLS exceptions can contain credentials or external file contents.
        print(
            "Deployment operation failed; check inputs, lock ownership and service health",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
