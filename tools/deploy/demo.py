"""Bounded demo lifecycle, explicit configuration identity, and secret-free provenance."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
from worker_status import WorkerProgressTracker, latest_worker_progress  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = ROOT
REVISION_LABEL = "org.opencontainers.image.revision"
APPLICATION_SERVICES = {"api", "worker", "frontend", "migrate", "schema", "seed"}
SOURCE_SERVICES = APPLICATION_SERVICES | {"collector", "local_alert_receiver"}
VENDOR_SERVICE_KEYS = {
    "mysql": "mysql",
    "gateway": "gateway",
    "gateway_log_rotate": "gateway",
    "prometheus": "prometheus",
    "loki": "loki",
    "tempo": "tempo",
    "grafana": "grafana",
    "alertmanager": "alertmanager",
}
ENV_FILES = (
    "PRL_DB_ENV_FILE",
    "PRL_RUNTIME_ENV_FILE",
    "PRL_MIGRATION_ENV_FILE",
    "PRL_SEED_ENV_FILE",
)
CONFIG_COMMON = (
    "tools/deploy/compose.yaml",
    "Dockerfile.backend",
    "Dockerfile.frontend",
    "tools/deploy/mysql/10-identities.sh",
    "tools/deploy/mysql/apply-grants.sh",
    "tools/deploy/mysql/grants.sql",
)
CONFIG_HTTPS = (
    "tools/deploy/compose.https.yaml",
    "tools/deploy/nginx/nginx.conf",
    "tools/deploy/nginx/https.conf",
    "tools/deploy/nginx/gateway-entrypoint.sh",
    "tools/deploy/nginx/proxy.conf",
    "tools/deploy/nginx/routes.conf",
    "tools/deploy/nginx/route-map.conf",
    "tools/deploy/nginx/file-sink.conf",
)
CONFIG_OBSERVABILITY = (
    "tools/deploy/compose.observability.yaml",
    "tools/deploy/nginx/file-sink-observability.conf",
    "tools/infra/images.lock.json",
    "tools/infra/collector.Dockerfile",
    "tools/infra/otel-collector.yaml",
    "tools/infra/prometheus.yml",
    "tools/infra/prometheus-rules.yml",
    "tools/infra/loki.yaml",
    "tools/infra/tempo.yaml",
    "tools/infra/alertmanager.yml",
    "tools/infra/grafana-datasources.yml",
    "tools/infra/gateway-log-rotate.sh",
    "src/backend/app/observability/alert_receiver.py",
)


class OperationFailed(Exception):
    pass


class ProvenanceMismatch(OperationFailed):
    pass


class ConfigurationIdentityMismatch(OperationFailed):
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


def _safe_file(name: str) -> Path:
    raw = os.environ.get(name, "")
    try:
        path = Path(raw)
        if not raw or not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK):
            raise OperationFailed()
        if path.stat().st_size <= 0:
            raise OperationFailed()
    except (OSError, ValueError):
        raise OperationFailed() from None
    return path


def _validate_grafana_credential_file(name: str) -> None:
    path = _safe_file(name)
    try:
        contents = path.read_bytes()
    except OSError:
        raise OperationFailed() from None
    if contents.endswith(b"\n"):
        contents = contents[:-1]
    if (
        not contents
        or b"\n" in contents
        or b"\r" in contents
        or b"\x00" in contents
        or b"\xef\xbb\xbf" in contents
    ):
        raise OperationFailed()
    try:
        contents.decode("utf-8")
    except UnicodeDecodeError:
        raise OperationFailed() from None


def _dns_hostname(host: str) -> str:
    if not host or any(ord(char) < 33 or ord(char) > 126 for char in host):
        raise OperationFailed()
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OperationFailed() from None
    if not ascii_host or len(ascii_host) > 253:
        raise OperationFailed()
    labels = ascii_host.split(".")
    if any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise OperationFailed()
    return ascii_host


def _normalize_origin(raw: str, *, operations: bool = False) -> tuple[str, str, int]:
    if not raw or any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in raw):
        raise OperationFailed()
    try:
        parsed = urlsplit(raw)
        port = parsed.port or 443
    except ValueError:
        raise OperationFailed() from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
        or "?" in raw
        or "#" in raw
    ):
        raise OperationFailed()
    if operations:
        try:
            ipaddress.ip_address(parsed.hostname)
            raise OperationFailed()
        except ValueError:
            pass
    host = _dns_hostname(parsed.hostname)
    normalized = f"https://{host}" + (f":{port}" if parsed.port is not None else "")
    return normalized, host, port


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isascii() or not raw.isdecimal():
        raise OperationFailed()
    value = int(raw)
    if not 1 <= value <= 65535:
        raise OperationFailed()
    return value


def _cidrs(raw: str) -> tuple[str, ...]:
    if not raw or len(raw) > 1024 or any(ord(char) < 32 or ord(char) > 126 for char in raw):
        raise OperationFailed()
    if not re.fullmatch(r"[0-9A-Fa-f:.,/ \t]+", raw):
        raise OperationFailed()
    parts = [part.strip() for part in raw.split(",")]
    if not parts or len(parts) > 16 or any(not part for part in parts):
        raise OperationFailed()
    normalized = []
    for part in parts:
        if any(char.isspace() for char in part):
            raise OperationFailed()
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise OperationFailed() from None
        if network.prefixlen == 0:
            raise OperationFailed()
        normalized.append(str(network))
    if len(set(normalized)) != len(normalized):
        raise OperationFailed()
    return tuple(sorted(normalized))


def _expected_worker_slots(raw: str) -> tuple[int, ...]:
    if not raw or len(raw) > 31:
        raise OperationFailed()
    parts = raw.split(",")
    if any(not re.fullmatch(r"[1-8]", part) for part in parts):
        raise OperationFailed()
    slots = tuple(sorted(int(part) for part in parts))
    if len(set(slots)) != len(parts):
        raise OperationFailed()
    return slots


def _configuration_identity(
    overlay: str,
    observability: bool,
    selections: dict[str, object],
) -> str:
    files = list(CONFIG_COMMON)
    if overlay == "local":
        files.append("tools/deploy/compose.local.yaml")
    else:
        files.extend(CONFIG_HTTPS)
    if observability:
        files.extend(CONFIG_OBSERVABILITY)
    inputs = []
    for relative in files:
        path = CONFIG_ROOT / relative
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise OperationFailed() from None
        inputs.append({"path": relative, "sha256": digest})
    payload = json.dumps(
        {"files": inputs, "selections": selections},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _image_lock() -> dict[str, str]:
    try:
        value = json.loads(
            (CONFIG_ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig")
        )
        if value.get("schema_version") != 1 or not isinstance(value.get("images"), dict):
            raise ValueError
        images = value["images"]
        if set(images) != set(VENDOR_SERVICE_KEYS.values()) | {"collector"}:
            raise ValueError
        if any(not isinstance(ref, str) or "@sha256:" not in ref for ref in images.values()):
            raise ValueError
        return images
    except (OSError, ValueError, TypeError, AttributeError):
        raise OperationFailed() from None


class Demo:
    def __init__(
        self,
        overlay,
        source_sha,
        observability=False,
        *,
        validate_grafana_credential_format=True,
    ):
        if (
            overlay not in {"local", "https"}
            or type(observability) is not bool
            or type(validate_grafana_credential_format) is not bool
        ):
            raise OperationFailed()
        if observability and overlay != "https":
            raise OperationFailed()
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise OperationFailed()
        self.overlay = overlay
        self.source_sha = source_sha
        self.observability = observability
        self.locked_images = _image_lock()
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
        if observability:
            self.base.extend(["-f", str(ROOT / "tools/deploy/compose.observability.yaml")])
        for name in ENV_FILES:
            raw = os.environ.get(name, "")
            try:
                path = Path(raw)
                if not raw or not path.is_absolute() or not path.is_file():
                    raise OperationFailed()
            except (OSError, ValueError):
                raise OperationFailed() from None
        self.compose_environment = {"SERVICE_RELEASE": source_sha}
        selections: dict[str, object] = {}
        if overlay == "https":
            for name in ("PRL_TLS_CERT_FILE", "PRL_TLS_KEY_FILE"):
                _safe_file(name)
            self.origin, app_host, app_port = _normalize_origin(
                os.environ.get("PRL_HTTPS_ORIGIN", "")
            )
            bind = os.environ.get("PRL_HTTPS_BIND", "127.0.0.1")
            try:
                bind = str(ipaddress.ip_address(bind))
            except ValueError:
                raise OperationFailed() from None
            port = _port("PRL_HTTPS_PORT", 8443)
            if port != app_port:
                raise OperationFailed()
            self.compose_environment.update(
                {
                    "PRL_HTTPS_ORIGIN": self.origin,
                    "PRL_HTTPS_BIND": bind,
                    "PRL_HTTPS_PORT": str(port),
                }
            )
            selections.update(
                {"application_origin": self.origin, "https_bind": bind, "https_port": port}
            )
        else:
            port = _port("PRL_HTTP_PORT", 8080)
            self.origin = f"http://localhost:{port}"
            selections["http_port"] = port
        if observability:
            for name in ("PRL_OPS_TLS_CERT_FILE", "PRL_OPS_TLS_KEY_FILE"):
                _safe_file(name)
            for name in (
                "PRL_GRAFANA_ADMIN_USER_FILE",
                "PRL_GRAFANA_ADMIN_PASSWORD_FILE",
            ):
                if validate_grafana_credential_format:
                    _validate_grafana_credential_file(name)
                else:
                    _safe_file(name)
            self.ops_origin, ops_host, ops_port = _normalize_origin(
                os.environ.get("PRL_OPS_ORIGIN", ""), operations=True
            )
            if ops_host == app_host or ops_port != app_port:
                raise OperationFailed()
            cidrs = _cidrs(os.environ.get("PRL_OPS_CIDRS", ""))
            slots = _expected_worker_slots(os.environ.get("PRL_EXPECTED_WORKER_SLOTS", "1"))
            self.compose_environment.update(
                {
                    "PRL_OPS_ORIGIN": self.ops_origin,
                    "PRL_OPS_HOST": ops_host,
                    "PRL_OPS_CIDRS": ",".join(cidrs),
                    "PRL_EXPECTED_WORKER_SLOTS": ",".join(map(str, slots)),
                }
            )
            selections.update(
                {
                    "operations_origin": self.ops_origin,
                    "operations_cidrs": list(cidrs),
                    "expected_worker_slots": list(slots),
                }
            )
        else:
            self.ops_origin = ""
        for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
            if os.environ.get(name):
                raise OperationFailed()
        self.configuration_sha256 = _configuration_identity(overlay, observability, selections)
        self.observability_result = None

    @contextmanager
    def _compose_environment(self):
        missing = object()
        prior = {name: os.environ.get(name, missing) for name in self.compose_environment}
        try:
            os.environ.update(self.compose_environment)
            yield
        finally:
            for name, value in prior.items():
                if value is missing:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def compose(self, *args, timeout=180):
        with self._compose_environment():
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
                if not re.fullmatch(r"[0-9a-f]{64}", ids):
                    tracker.observe(None, running=False)
                    return False
                running = run(["docker", "inspect", "--format", "{{.State.Running}}", ids])
                if running != "true":
                    tracker.observe(None, running=False)
                    return False
                logs = run(["docker", "logs", "--tail", "200", ids])
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
        if service in SOURCE_SERVICES:
            if (image.get("Labels") or {}).get(REVISION_LABEL) != self.source_sha:
                raise ProvenanceMismatch()
        return image

    def _configuration(self, services: Iterable[str] = ()):
        services = tuple(services)
        profiles = []
        if self.observability:
            profiles.extend(("operations", "seed"))
        else:
            profiles.extend(
                profile
                for service, profile in (("migrate", "operations"), ("seed", "seed"))
                if service in services
            )
        profile_args = [argument for profile in profiles for argument in ("--profile", profile)]
        return json.loads(
            self.compose(
                *profile_args,
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            )
        )

    def _check_locked_reference(self, service, reference):
        lock_key = VENDOR_SERVICE_KEYS.get(service)
        if not self.observability or lock_key is None:
            return
        if reference != self.locked_images[lock_key]:
            raise ProvenanceMismatch()

    def require_images(self, services: Iterable[str]) -> None:
        services = tuple(services)
        configuration = self._configuration(services)
        for service in services:
            reference = configuration["services"][service]["image"]
            self._check_locked_reference(service, reference)
            self.inspect_image(reference, service)

    def images(self, configured_only=False):
        configuration = self._configuration()
        configured = configuration["services"]
        names = set(configured) & evidence.V2_SERVICES if self.observability else evidence.SERVICES
        images = {}
        for service in sorted(names):
            if service not in configured:
                if self.observability:
                    raise OperationFailed()
                continue
            reference = configured[service]["image"]
            self._check_locked_reference(service, reference)
            identifier = "" if configured_only else self.compose("images", "-q", service)
            if identifier and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{12,64}", identifier):
                raise OperationFailed()
            image = self.inspect_image(identifier or reference, service)
            images[service] = {
                "id": image["Id"],
                "digests": image.get("RepoDigests") or [],
            }
        return images

    def _telemetry_evidence(self):
        code = (
            "from app.observability.alert_receiver import telemetry_status; "
            "healthy, _ = telemetry_status(timeout=0.5); "
            "print('passed' if healthy else 'degraded')"
        )
        try:
            result = self.compose(
                "exec", "-T", "local_alert_receiver", "python", "-c", code, timeout=30
            )
        except (OperationFailed, OSError, subprocess.TimeoutExpired):
            return {"command": "evidence", "status": "failed"}
        if result == "passed":
            return {"command": "evidence", "status": "passed"}
        if result == "degraded":
            return {"command": "evidence", "status": "degraded"}
        return {"command": "evidence", "status": "failed"}

    def execute(self, command):
        self.compose("config", "--quiet")
        if command == "build":
            targets = ["api", "frontend"]
            if self.observability:
                targets.append("collector")
            self.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.source_sha}",
                *targets,
                timeout=900,
            )
            self.require_images(targets)
        elif command in {"migrate", "seed", "up"}:
            if self.observability and command == "up":
                services = sorted(set(self._configuration()["services"]) & evidence.V2_SERVICES)
            else:
                services = (
                    ["schema", command]
                    if command in {"migrate", "seed"}
                    else ["schema", "api", "worker", "frontend"]
                )
            self.require_images(services)
            self.database()
            if command == "migrate":
                self.compose("run", "--rm", "migrate")
                self.compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh")
            self.compose("run", "--rm", "schema")
            if command == "seed":
                self.compose("run", "--rm", "seed")
            elif command == "up":
                services = ["api", "frontend", "gateway", "worker"]
                if self.observability:
                    services.extend(
                        [
                            "collector",
                            "prometheus",
                            "loki",
                            "tempo",
                            "grafana",
                            "alertmanager",
                            "local_alert_receiver",
                            "gateway_log_rotate",
                        ]
                    )
                self.compose("up", "-d", *services)
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
                if service in evidence.V2_SERVICES:
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
            if self.observability:
                self.observability_result = self._telemetry_evidence()
        elif command == "stop":
            self.compose("stop")
        elif command == "down":
            self.compose("down")


def require_clean_source(expected_sha):
    if run(["git", "status", "--porcelain", "--untracked-files=all"]):
        raise OperationFailed()
    if run(["git", "rev-parse", "HEAD"]) != expected_sha:
        raise OperationFailed()


def _parse_args(args):
    if (
        len(args) not in (3, 4)
        or args[0] not in evidence.COMMANDS
        or args[1] != "--overlay"
        or args[2] not in {"local", "https"}
        or (len(args) == 4 and args[3] != "--observability")
        or (len(args) == 4 and args[2] != "https")
    ):
        return None
    return args[0], args[2], len(args) == 4


def _previous_mode(previous):
    if previous is None:
        return None
    return previous["overlay"], previous.get("observability", False)


def _mode_matches(previous, overlay, observability, configuration_sha256):
    if previous is None:
        return True
    old_overlay, old_observability = _previous_mode(previous)
    if previous["manifest_version"] == 1:
        return old_overlay == overlay and observability is False
    return (
        old_overlay == overlay
        and old_observability is observability
        and previous["configuration_sha256"] == configuration_sha256
    )


def _record_state(command, previous):
    if command == "up":
        return "active"
    if command == "stop":
        return "stopped"
    if command == "down":
        return "down"
    if previous is None:
        return "unknown"
    if previous["manifest_version"] == 1:
        return "unknown"
    return previous["deployment_state"]


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    parsed = _parse_args(args)
    if parsed is None:
        print(
            "Usage: demo.sh {config|build|migrate|seed|up|stop|down|status|evidence} "
            "--overlay {local|https} [--observability (HTTPS only)]",
            file=sys.stderr,
        )
        return 2
    command, requested_overlay, requested_observability = parsed
    if requested_observability and requested_overlay != "https":
        return 2
    try:
        with evidence.lifecycle_lock(ROOT / "build/deploy/.lifecycle.lock"):
            sha = run(["git", "rev-parse", "HEAD"])
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise OperationFailed()
            require_clean_source(sha)
            path = ROOT / "build/deploy" / sha / "manifest.json"
            try:
                previous = evidence.validate(json.loads(path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                previous = None
            except (ValueError, TypeError, KeyError):
                raise OperationFailed() from None
            if previous is not None and previous["source_sha"] != sha:
                raise OperationFailed()
            cleanup = command in {"status", "stop", "down"}
            if cleanup and previous is not None:
                selected_overlay, selected_observability = _previous_mode(previous)
            else:
                selected_overlay, selected_observability = (
                    requested_overlay,
                    requested_observability,
                )
            demo = Demo(
                selected_overlay,
                sha,
                observability=selected_observability,
                validate_grafana_credential_format=not cleanup,
            )
            if (
                not cleanup
                and previous is not None
                and previous.get("deployment_state") != "down"
                and not _mode_matches(
                    previous,
                    selected_overlay,
                    selected_observability,
                    demo.configuration_sha256,
                )
            ):
                raise ConfigurationIdentityMismatch()
            try:
                demo.execute(command)
                if command == "config":
                    images = {}
                elif cleanup and command in {"stop", "down"} and previous is not None:
                    images = previous["images"]
                else:
                    images = demo.images(configured_only=command == "build")
                require_clean_source(sha)
            except ConfigurationIdentityMismatch:
                raise
            except Exception:
                if cleanup and previous is not None:
                    failed_overlay, failed_observability = _previous_mode(previous)
                    failed_hash = (
                        previous["configuration_sha256"]
                        if previous["manifest_version"] == 2
                        else demo.configuration_sha256
                    )
                    failed_images = previous["images"]
                    failed_state = _record_state(command, previous)
                    failed_telemetry = (
                        previous.get("observability_verification")
                        if previous["manifest_version"] == 2
                        else None
                    )
                else:
                    failed_overlay = demo.overlay
                    failed_observability = demo.observability
                    failed_hash = demo.configuration_sha256
                    failed_images = {}
                    failed_state = _record_state(command, previous)
                    failed_telemetry = (
                        previous.get("observability_verification")
                        if previous is not None and previous["manifest_version"] == 2
                        else None
                    )
                evidence.write(
                    path,
                    evidence.create_v2(
                        sha,
                        failed_overlay,
                        failed_observability,
                        failed_hash,
                        command,
                        "failed",
                        failed_images,
                        observability_verification=failed_telemetry,
                        deployment_state=failed_state,
                        previous=previous,
                    ),
                )
                raise
            if cleanup and previous is not None:
                record_overlay, record_observability = _previous_mode(previous)
                record_hash = (
                    previous["configuration_sha256"]
                    if previous["manifest_version"] == 2
                    else demo.configuration_sha256
                )
            else:
                record_overlay = demo.overlay
                record_observability = demo.observability
                record_hash = demo.configuration_sha256
            if demo.observability_result is not None:
                telemetry_check = demo.observability_result
            elif (
                previous is not None
                and previous["manifest_version"] == 2
                and previous["observability"] is record_observability
                and previous["overlay"] == record_overlay
                and previous["configuration_sha256"] == record_hash
            ):
                telemetry_check = previous["observability_verification"]
            else:
                telemetry_check = None
            state = _record_state(command, previous)
            if command == "down":
                state = "down"
            evidence.write(
                path,
                evidence.create_v2(
                    sha,
                    record_overlay,
                    record_observability,
                    record_hash,
                    command,
                    "passed",
                    images,
                    observability_verification=telemetry_check,
                    deployment_state=state,
                    previous=previous,
                ),
            )
        telemetry_label = (
            f"; telemetry={demo.observability_result['status']}"
            if demo.observability_result is not None
            else ""
        )
        print(
            f"Deployment {command} passed; business verification=passed{telemetry_label}; "
            f"evidence: build/deploy/{sha}/manifest.json"
        )
        return 0
    except ConfigurationIdentityMismatch:
        print(
            "Deployment mode or public configuration differs from recorded evidence; "
            "run down with the recorded selection before changing it",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "Deployment operation failed; check inputs, lock ownership and service health",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
