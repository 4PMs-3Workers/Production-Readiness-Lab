"""Non-destructive real application acceptance using a UUID-owned Compose project.

Opt in with:
RUN_COMPOSE_ACCEPTANCE=1 python -m pytest -q --tb=short
tests/containers/test_compose_acceptance.py.
This suite never uses the prl-demo project or the API E2E reset harness.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from tools.deploy.worker_status import WorkerProgress, WorkerProgressTracker, latest_worker_progress

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tools/deploy"
PLAYWRIGHT_CONFIG = ROOT / "src/frontend/playwright.compose.config.ts"
COMPOSE_FILES = (DEPLOY / "compose.yaml", DEPLOY / "compose.local.yaml")
SCHEMA_REVISION = "20261019_1200_proposal_replay"
REVISION_LABEL = "org.opencontainers.image.revision"
GATEWAY_SUBNET = ipaddress.ip_network("172.30.34.0/29")
PROJECT_PREFIX = "prl-task6-"
RUN = os.environ.get("RUN_COMPOSE_ACCEPTANCE") == "1"


class AcceptanceFailure(RuntimeError):
    """An intentionally sanitized failure from the local acceptance harness."""


def invoke(
    args: list[str],
    *,
    cwd: Path = ROOT,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout: float = 180,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceFailure("A bounded local acceptance command timed out") from None
    except OSError:
        raise AcceptanceFailure("A required local acceptance command could not start") from None


def require_success(result: subprocess.CompletedProcess[str], description: str) -> str:
    if result.returncode != 0:
        raise AcceptanceFailure(description)
    return result.stdout.strip()


def json_output(result: subprocess.CompletedProcess[str], description: str):
    try:
        return json.loads(require_success(result, description))
    except (json.JSONDecodeError, TypeError):
        raise AcceptanceFailure(description) from None


def one_volume(name: str, labels: dict[str, str] | None = None) -> dict[str, object]:
    result = invoke(["docker", "volume", "inspect", name])
    if result.returncode != 0:
        raise AcceptanceFailure("The test-owned persistent database volume is unavailable")
    records = json_output(result, "The test-owned database volume could not be inspected")
    if len(records) != 1 or records[0].get("Name") != name:
        raise AcceptanceFailure("The test-owned database volume identity changed")
    volume = records[0]
    actual_labels = volume.get("Labels") or {}
    if labels is not None and any(actual_labels.get(key) != value for key, value in labels.items()):
        raise AcceptanceFailure("A database volume failed its ownership check")
    return {
        "name": volume.get("Name"),
        "driver": volume.get("Driver"),
        "mountpoint": volume.get("Mountpoint"),
        "labels": actual_labels,
    }


class ComposeAcceptance:
    def __init__(self, scratch: Path) -> None:
        self.project = PROJECT_PREFIX + uuid4().hex
        self.image_tag = "task6-" + uuid4().hex[:20]
        self.port = self._free_port()
        self.origin = f"http://localhost:{self.port}"
        self.volume_name = f"{self.project}_mysql_data"
        self.expected_sha = self._source_revision()
        self.seed_passwords = {
            role: secrets.token_urlsafe(30) for role in ("ADMIN", "STAFF", "STUDENT")
        }
        self.student_email = "nhien.nguyen@demo.example.invalid"
        self.student_password = self.seed_passwords["STUDENT"]
        self.worker_student_email = "linh.tran@demo.example.invalid"
        self.staff_email = "ha.le@demo.example.invalid"
        self.staff_password = self.seed_passwords["STAFF"]
        self.db_passwords = {
            "root": secrets.token_hex(32),
            "migration": secrets.token_urlsafe(32),
            "runtime": secrets.token_urlsafe(32),
            "seed": secrets.token_urlsafe(32),
        }
        self.scratch = scratch
        self.env_files = self._write_env_files()
        self.env = dict(os.environ)
        for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
            self.env.pop(name, None)
        self.env.update(self.env_files)
        self.env.update(
            PRL_IMAGE_TAG=self.image_tag,
            PRL_HTTP_PORT=str(self.port),
            DOCKER_DEFAULT_PLATFORM="linux/amd64",
        )
        self.base = ["docker", "compose", "--project-name", self.project]
        for compose_file in COMPOSE_FILES:
            self.base.extend(["-f", str(compose_file)])
        self.demo_snapshot: tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None = None
        self.started = False
        self._ensure_unique_ownership()
        self._ensure_local_docker()
        self.demo_snapshot = self._project_snapshot("prl-demo")
        self._ensure_gateway_subnet_available()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _source_revision() -> str:
        result = invoke(["git", "rev-parse", "HEAD"])
        revision = require_success(result, "The source revision could not be established")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise AcceptanceFailure("The source revision is not a full Git SHA")
        return revision

    def _write_env_files(self) -> dict[str, str]:
        raw = {
            "db": {
                "MYSQL_ROOT_PASSWORD": self.db_passwords["root"],
                "PRL_MIGRATION_PASSWORD": self.db_passwords["migration"],
                "PRL_RUNTIME_PASSWORD": self.db_passwords["runtime"],
                "PRL_SEED_PASSWORD": self.db_passwords["seed"],
            },
            "runtime": {
                "DATABASE_URL": (
                    "mysql+asyncmy://prl_runtime:"
                    f"{quote(self.db_passwords['runtime'], safe='')}@mysql/prl"
                ),
                "APPLICATION_ORIGIN": self.origin,
                "COOKIE_SECRET": secrets.token_hex(32),
                "CSRF_SECRET": secrets.token_hex(32),
                "ENVIRONMENT": "local",
                "COOKIE_SECURE": "false",
                "DB_CONNECT_TIMEOUT_SECONDS": "1",
                "DB_POOL_TIMEOUT_SECONDS": "0.5",
                "DB_QUERY_TIMEOUT_SECONDS": "1",
                "DB_LOCK_TIMEOUT_SECONDS": "0.5",
                "REQUEST_TIMEOUT_SECONDS": "3",
                "READINESS_TIMEOUT_SECONDS": "2",
                "WORKER_POLL_INTERVAL_SECONDS": "0.5",
                "WORKER_HEARTBEAT_INTERVAL_SECONDS": "2",
            },
            "migration": {
                "DATABASE_URL": (
                    "mysql+asyncmy://prl_migration:"
                    f"{quote(self.db_passwords['migration'], safe='')}@mysql/prl"
                )
            },
            "seed": {
                "DATABASE_URL": (
                    "mysql+asyncmy://prl_seed:"
                    f"{quote(self.db_passwords['seed'], safe='')}@mysql/prl"
                ),
                "ENVIRONMENT": "local",
                "DEMO_SEED_DATABASE": "prl",
                **{
                    f"DEMO_{role}_PASSWORD": password
                    for role, password in self.seed_passwords.items()
                },
            },
        }
        variables = {}
        for role, entries in raw.items():
            path = self.scratch / f"{role}.env"
            path.write_text("".join(f"{name}={value}\n" for name, value in entries.items()))
            variables[f"PRL_{role.upper()}_ENV_FILE"] = str(path.resolve())
        return variables

    def _ensure_unique_ownership(self) -> None:
        if self.project == "prl-demo" or not re.fullmatch(r"prl-task6-[0-9a-f]{32}", self.project):
            raise AcceptanceFailure("The Compose acceptance project name is not test-owned")
        if self.image_tag.startswith("local") or not re.fullmatch(
            r"task6-[0-9a-f]{20}", self.image_tag
        ):
            raise AcceptanceFailure("The Compose acceptance image tag is not test-owned")
        containers = invoke(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"]
        )
        if containers.returncode == 0 and containers.stdout.strip():
            raise AcceptanceFailure("A container already uses the generated acceptance project")
        volumes = invoke(["docker", "volume", "inspect", self.volume_name])
        if volumes.returncode == 0:
            raise AcceptanceFailure("A volume already uses the generated acceptance project")
        for image in (f"prl-backend:{self.image_tag}", f"prl-frontend:{self.image_tag}"):
            if invoke(["docker", "image", "inspect", image]).returncode == 0:
                raise AcceptanceFailure("An image already uses the generated acceptance tag")

    @staticmethod
    def _ensure_local_docker() -> None:
        result = invoke(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
        host = require_success(result, "A local Docker context is required for acceptance")
        if not host.startswith(("npipe://", "unix://")):
            raise AcceptanceFailure("The selected Docker context is not a local engine")
        require_success(
            invoke(["docker", "info", "--format", "{{.ServerVersion}}"]),
            "Docker is unavailable",
        )

    @staticmethod
    def _project_snapshot(
        project: str,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        containers_result = invoke(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
        )
        containers = tuple(sorted(filter(None, containers_result.stdout.splitlines())))
        volumes_result = invoke(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ]
        )
        if volumes_result.returncode != 0:
            raise AcceptanceFailure("The existing demo volume snapshot could not be read")
        identities = []
        for name in filter(None, volumes_result.stdout.splitlines()):
            identity = one_volume(name)
            identities.append((name, json.dumps(identity, sort_keys=True)))
        return containers, tuple(sorted(identities))

    def _ensure_gateway_subnet_available(self) -> None:
        listed = invoke(["docker", "network", "ls", "-q"])
        identifiers = require_success(listed, "Docker networks could not be inspected").splitlines()
        for identifier in filter(None, identifiers):
            inspected = json_output(
                invoke(["docker", "network", "inspect", identifier]),
                "Docker network ownership could not be verified",
            )
            if len(inspected) != 1:
                raise AcceptanceFailure("A Docker network identity could not be verified")
            network = inspected[0]
            labels = network.get("Labels") or {}
            if labels.get("com.docker.compose.project") == self.project:
                raise AcceptanceFailure("A generated project network already exists")
            for configuration in (network.get("IPAM") or {}).get("Config") or []:
                subnet = configuration.get("Subnet")
                if not subnet:
                    continue
                try:
                    if ipaddress.ip_network(subnet, strict=False).overlaps(GATEWAY_SUBNET):
                        raise AcceptanceFailure("The dedicated gateway subnet is already in use")
                except ValueError:
                    raise AcceptanceFailure(
                        "A Docker network has an invalid subnet declaration"
                    ) from None

    def compose(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: float = 180,
    ) -> subprocess.CompletedProcess[str]:
        if self.project == "prl-demo" or self.base[3] != self.project:
            raise AcceptanceFailure("A Compose action failed its project ownership guard")
        return invoke(self.base + list(args), env=self.env, input_text=input_text, timeout=timeout)

    def expect_compose_failure(self, *args: str, timeout: float = 180) -> None:
        result = self.compose(*args, timeout=timeout)
        if result.returncode == 0:
            raise AcceptanceFailure(
                "A deliberately incompatible Compose operation unexpectedly passed"
            )

    def sql(self, statement: str) -> str:
        result = self.compose(
            "exec",
            "-T",
            "mysql",
            "bash",
            "-c",
            'MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql --protocol=socket -uroot '
            "--connect-timeout=3 --batch --raw --skip-column-names prl",
            input_text=statement,
            timeout=30,
        )
        return require_success(result, "A test-owned database assertion failed")

    def api_client(self) -> httpx.Client:
        return httpx.Client(base_url=self.origin, timeout=5, trust_env=False)

    def http(self, method: str, path: str, *, timeout: float = 5, **kwargs) -> httpx.Response:
        try:
            with httpx.Client(base_url=self.origin, timeout=timeout, trust_env=False) as client:
                return client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise AcceptanceFailure("A bounded gateway request failed") from None

    def wait_for(self, predicate: Callable[[], bool], *, seconds: float, failure: str) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (AcceptanceFailure, httpx.HTTPError, OSError, ValueError):
                pass
            time.sleep(0.5)
        raise AcceptanceFailure(failure)

    def start(self) -> None:
        require_success(self.compose("config", "--quiet"), "The isolated Compose config is invalid")
        config = json_output(
            self.compose(
                "--profile",
                "operations",
                "--profile",
                "seed",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ),
            "The isolated Compose configuration could not be inspected",
        )
        services = config["services"]
        if {name for name, service in services.items() if service.get("ports")} != {"gateway"}:
            raise AcceptanceFailure("Only the local gateway may publish a test port")
        self.require_service_image_config(services)

        require_success(
            self.compose(
                "build",
                "--build-arg",
                f"PRL_SOURCE_SHA={self.expected_sha}",
                "api",
                "frontend",
                timeout=900,
            ),
            "The isolated application images could not be built",
        )
        require_success(
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", "mysql", timeout=240),
            "The isolated database did not become healthy",
        )
        self.expect_compose_failure("run", "--rm", "-T", "schema")
        require_success(
            self.compose("--profile", "operations", "run", "--rm", "-T", "migrate", timeout=240),
            "The isolated database migration failed",
        )
        require_success(
            self.compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh", timeout=45),
            "The isolated runtime grants could not be installed",
        )
        require_success(
            self.compose("run", "--rm", "-T", "schema", timeout=45),
            "The migrated database failed its schema check",
        )
        require_success(
            self.compose("--profile", "seed", "run", "--rm", "-T", "seed", timeout=180),
            "The isolated demo seed failed",
        )
        require_success(
            self.compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
                "frontend",
                "gateway",
                "worker",
                timeout=240,
            ),
            "The isolated application services did not become healthy",
        )
        self.started = True
        self.wait_gateway()
        self.require_worker_identity()
        self.wait_heartbeat()
        self.volume_before = one_volume(
            self.volume_name,
            {
                "com.docker.compose.project": self.project,
                "com.docker.compose.volume": "mysql_data",
            },
        )

    @staticmethod
    def require_service_image_config(services: dict[str, object]) -> None:
        if services["api"]["image"] != services["worker"]["image"]:
            raise AcceptanceFailure("API and worker do not select the same backend image")
        if services["api"]["build"] != services["worker"]["build"]:
            raise AcceptanceFailure("API and worker do not share a backend build definition")
        for name in ("api", "worker"):
            revisions = services[name].get("environment", {}).get("ACCEPTED_SCHEMA_REVISIONS")
            if revisions != SCHEMA_REVISION:
                raise AcceptanceFailure("A runtime service accepts the wrong schema revision")

    def wait_gateway(self) -> None:
        def ready() -> bool:
            gateway = self.http("GET", "/health/gateway", timeout=3)
            api = self.http("GET", "/health/ready", timeout=3)
            return gateway.status_code == 200 and api.status_code == 200

        self.wait_for(ready, seconds=90, failure="The local gateway/API readiness check timed out")

    def require_worker_identity(self) -> tuple[str, str]:
        api_id = require_success(self.compose("ps", "-q", "api"), "The API container is missing")
        worker_id = require_success(
            self.compose("ps", "-q", "worker"), "The worker container is missing"
        )
        if (
            api_id == worker_id
            or not re.fullmatch(r"[0-9a-f]{64}", api_id)
            or not re.fullmatch(r"[0-9a-f]{64}", worker_id)
        ):
            raise AcceptanceFailure("The API and worker do not run as separate containers")
        records = json_output(
            invoke(["docker", "inspect", api_id, worker_id]),
            "The API and worker image provenance could not be inspected",
        )
        if len(records) != 2:
            raise AcceptanceFailure(
                "The API and worker container identities could not be inspected"
            )
        api, worker = records
        if api.get("Image") != worker.get("Image"):
            raise AcceptanceFailure("The API and worker image IDs differ")
        labels = (api.get("Config") or {}).get("Labels") or {}
        if labels.get(REVISION_LABEL) != self.expected_sha:
            raise AcceptanceFailure("The shared backend image is not labeled with this source SHA")
        if (worker.get("Config") or {}).get("Cmd") != ["python", "-m", "app.worker"]:
            raise AcceptanceFailure("The worker does not run the reviewed worker command")
        if (api.get("Config") or {}).get("Cmd") != [
            "python",
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "src/backend",
            "--no-proxy-headers",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]:
            raise AcceptanceFailure("The API does not run the shared image's default API command")
        return api_id, worker_id

    def worker_log_output(self) -> str:
        result = self.compose(
            "logs",
            "--no-color",
            "--no-log-prefix",
            "--tail",
            "400",
            "worker",
            timeout=30,
        )
        return require_success(result, "The test-owned worker progress could not be read")

    def worker_records(self) -> list[dict[str, object]]:
        records = []
        for line in self.worker_log_output().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("event"):
                records.append(record)
        return records

    def wait_heartbeat(self, *, excluded_worker_id: str | None = None) -> WorkerProgress:
        tracker = WorkerProgressTracker(excluded_worker_id=excluded_worker_id)
        found: WorkerProgress | None = None

        def search() -> bool:
            nonlocal found
            try:
                worker_id = require_success(
                    self.compose("ps", "-q", "worker"), "The worker container is missing"
                )
                if not re.fullmatch(r"[0-9a-f]{64}", worker_id):
                    tracker.observe(None, running=False)
                    return False
                running = require_success(
                    invoke(["docker", "inspect", "--format", "{{.State.Running}}", worker_id]),
                    "The worker container could not be inspected",
                )
                if running != "true":
                    tracker.observe(None, running=False)
                    return False
                progress = latest_worker_progress(self.worker_log_output(), require_heartbeat=False)
            except AcceptanceFailure:
                tracker.observe(None, running=False)
                return False
            if tracker.observe(progress, running=True):
                found = progress
                return True
            return False

        self.wait_for(
            search,
            seconds=45,
            failure="The worker did not report fresh heartbeat/progress",
        )
        assert found is not None
        return found

    def login(self, client: httpx.Client, email: str, password: str) -> str:
        try:
            response = client.post(
                "/api/v1/auth/login",
                headers={"Origin": self.origin},
                json={"email": email, "password": password},
            )
            if response.status_code != 200:
                raise AcceptanceFailure("A seeded acceptance account could not authenticate")
            csrf = client.get("/api/v1/auth/csrf")
            if csrf.status_code != 200:
                raise AcceptanceFailure("A seeded acceptance account could not obtain CSRF state")
            return str(csrf.json()["csrf_token"])
        except (httpx.HTTPError, KeyError, ValueError):
            raise AcceptanceFailure("A seeded acceptance account could not authenticate") from None

    def create_worker_case(self) -> tuple[str, str, str, str]:
        with self.api_client() as student:
            student_csrf = self.login(
                student, self.worker_student_email, self.seed_passwords["STUDENT"]
            )
            context_response = student.get("/api/v1/booking-context")
            if context_response.status_code != 200:
                raise AcceptanceFailure("The isolated booking calendar is unavailable")
            context = context_response.json()
            purpose_response = student.get("/api/v1/purposes", params={"page": 1, "page_size": 50})
            if purpose_response.status_code != 200:
                raise AcceptanceFailure("The isolated consultation catalog is unavailable")
            purpose = next(
                item
                for item in purpose_response.json()["items"]
                if item["code"] == "CAREER_INTERNSHIP"
            )
            staff_response = student.get(
                "/api/v1/staff",
                params={"purpose_id": purpose["id"], "page": 1, "page_size": 50},
            )
            if staff_response.status_code != 200:
                raise AcceptanceFailure("The isolated staff catalog is unavailable")
            staff = next(
                item for item in staff_response.json()["items"] if item["name"] == "Lê Thu Hà"
            )
            first_date = date.fromisoformat(context["min_booking_date"])
            last_date = date.fromisoformat(context["max_booking_date"])
            selected_slots = None
            selected_date = first_date
            while selected_date <= last_date:
                available = student.get(
                    "/api/v1/availability",
                    params={
                        "purpose_id": purpose["id"],
                        "staff_id": staff["id"],
                        "date": selected_date.isoformat(),
                    },
                )
                if available.status_code != 200:
                    raise AcceptanceFailure("The isolated staff calendar is unavailable")
                slots = available.json()["slots"]
                if len(slots) >= 2:
                    selected_slots = slots[:2]
                    break
                selected_date += timedelta(days=1)
            if selected_slots is None:
                raise AcceptanceFailure("The isolated calendar has no pair of valid test slots")
            booking_response = student.post(
                "/api/v1/appointments",
                headers={
                    "Origin": self.origin,
                    "X-CSRF-Token": student_csrf,
                    "Idempotency-Key": f"compose-task6-{uuid4().hex}",
                },
                json={
                    "purpose_id": purpose["id"],
                    "staff_id": staff["id"],
                    "starts_at": selected_slots[0]["starts_at"],
                },
            )
            if booking_response.status_code != 201:
                raise AcceptanceFailure("The worker acceptance appointment could not be created")
            appointment_id = str(booking_response.json()["id"])

        with self.api_client() as staff_client:
            staff_csrf = self.login(staff_client, self.staff_email, self.staff_password)
            proposal_response = staff_client.post(
                f"/api/v1/staff/me/appointments/{appointment_id}/change-proposals",
                headers={
                    "Origin": self.origin,
                    "X-CSRF-Token": staff_csrf,
                    "Idempotency-Key": f"compose-task6-{uuid4().hex}",
                },
                json={
                    "expected_proposal_version": 0,
                    "expected_pending_proposal_id": None,
                    "proposed_starts_at": selected_slots[1]["starts_at"],
                    "proposed_location": None,
                },
            )
            if proposal_response.status_code != 201:
                raise AcceptanceFailure("The isolated staff change proposal could not be created")
            proposal_id = str(proposal_response.json()["id"])
        if not re.fullmatch(r"[0-9a-f-]{36}", appointment_id, re.IGNORECASE) or not re.fullmatch(
            r"[0-9a-f-]{36}", proposal_id, re.IGNORECASE
        ):
            raise AcceptanceFailure("The worker acceptance record returned an invalid identifier")
        return appointment_id, proposal_id, str(staff["name"]), selected_date.isoformat()

    def run_playwright(self, appointment_id: str, staff_name: str, booking_date: str) -> None:
        env = dict(self.env)
        env.update(
            PRL_COMPOSE_BASE_URL=self.origin,
            PRL_SMOKE_STUDENT_EMAIL=self.student_email,
            PRL_SMOKE_STUDENT_PASSWORD=self.student_password,
            PRL_SMOKE_STAFF_EMAIL=self.staff_email,
            PRL_SMOKE_STAFF_PASSWORD=self.staff_password,
            PRL_SMOKE_APPOINTMENT_ID=appointment_id,
            PRL_SMOKE_STAFF_NAME=staff_name,
            PRL_SMOKE_BOOKING_DATE=booking_date,
        )
        node = shutil.which("node")
        if not node:
            raise AcceptanceFailure(
                "Node.js is required for the dedicated Compose Playwright smoke"
            )
        args = [
            node,
            str(ROOT / "node_modules/@playwright/test/cli.js"),
            "test",
            "--config",
            str(PLAYWRIGHT_CONFIG),
        ]
        result = invoke(
            args,
            env=env,
            timeout=240,
        )
        if result.returncode != 0:
            raise AcceptanceFailure(
                "The real gateway student/staff browser smoke failed\n"
                + self._playwright_failure_summary(result)
            )

    def _playwright_failure_summary(self, result: subprocess.CompletedProcess[str]) -> str:
        output = f"{result.stdout}\n{result.stderr}"
        output = re.sub(r"\x1b\[[0-9;]*m", "", output)
        for secret in (
            self.origin,
            self.student_email,
            self.worker_student_email,
            self.staff_email,
            self.student_password,
            self.staff_password,
            *self.seed_passwords.values(),
            *self.db_passwords.values(),
            str(self.scratch),
            str(ROOT),
        ):
            if secret:
                output = output.replace(secret, "<redacted>")
        output = re.sub(
            r"https?://[^/\s\"']+(/[^?\s\"']*)?(?:\?[^\s\"']*)?",
            lambda match: f"<gateway>{match.group(1) or '/'}",
            output,
        )
        output = re.sub(
            r"(?i)(password|csrf|cookie|authorization|secret|token)\s*[:=]\s*[^\s,]+",
            r"\1=<redacted>",
            output,
        )
        output = re.sub(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            "<id>",
            output,
            flags=re.IGNORECASE,
        )
        safe_lines = []
        for line in output.splitlines():
            if any(
                marker in line
                for marker in (
                    "  1) ",
                    "  2) ",
                    "Error:",
                    "Expected",
                    "Received",
                    "Timeout",
                    "Call log:",
                    "waiting for",
                    "real-demo.spec.ts:",
                )
            ):
                safe_lines.append(line)
        return "\n".join(safe_lines[-24:]) or "No safe Playwright diagnostic line was available."

    def require_appointments_after_restart(self, expected_status: str) -> None:
        with self.api_client() as client:
            csrf = self.login(client, self.student_email, self.student_password)
            del csrf
            response = client.get("/api/v1/appointments", params={"page": 1, "page_size": 50})
            if response.status_code != 200:
                raise AcceptanceFailure("Persisted student history was not available after restart")
            items = response.json()["items"]
            if not any(item.get("status") == expected_status for item in items):
                raise AcceptanceFailure("Persisted student booking state changed after restart")

    def expire_worker_case(self, appointment_id: str, proposal_id: str) -> None:
        initial = self.wait_heartbeat()
        old_worker_id = initial.worker_id
        require_success(
            self.compose("stop", "--timeout", "20", "worker", timeout=45),
            "The test-owned worker could not be stopped for the overdue-proposal setup",
        )
        self.sql(
            "UPDATE change_proposals "
            "SET expires_at = created_at + INTERVAL 2 SECOND "
            f"WHERE id = UNHEX(REPLACE('{proposal_id}', '-', '')) AND status = 'PENDING';"
        )
        if (
            self.sql(
                "SELECT IF(expires_at > created_at, 1, 0) FROM change_proposals "
                f"WHERE id = UNHEX(REPLACE('{proposal_id}', '-', ''));"
            )
            != "1"
        ):
            raise AcceptanceFailure(
                "The disposable proposal expiry did not preserve its data check"
            )
        time.sleep(3)
        require_success(
            self.compose("start", "worker", timeout=45),
            "The test-owned worker could not restart",
        )
        resumed = self.wait_heartbeat(excluded_worker_id=old_worker_id)
        new_worker_id = resumed.worker_id
        if new_worker_id == old_worker_id:
            raise AcceptanceFailure("The worker did not resume in a new process identity")

        def expired() -> bool:
            status = self.sql(
                "SELECT status FROM change_proposals "
                f"WHERE id = UNHEX(REPLACE('{proposal_id}', '-', ''));"
            )
            if status != "EXPIRED":
                return False
            audit_count = self.sql(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE action = 'CHANGE_PROPOSAL_EXPIRED' "
                f"AND entity_id = '{proposal_id}';"
            )
            notice_count = self.sql(
                "SELECT COUNT(*) FROM notifications "
                "WHERE event_type = 'CHANGE_PROPOSAL_EXPIRED' "
                f"AND appointment_id = UNHEX(REPLACE('{appointment_id}', '-', ''));"
            )
            return audit_count == "1" and notice_count == "2"

        self.wait_for(
            expired,
            seconds=40,
            failure="The restarted worker did not make the exact expiry audit/notification effects",
        )
        records = self.worker_records()
        candidate = next(
            (
                item
                for item in reversed(records)
                if item.get("event") == "candidate"
                and item.get("worker_id") == new_worker_id
                and (item.get("candidate") or {}).get("outcome") == "EXPIRED"
            ),
            None,
        )
        if candidate is None:
            raise AcceptanceFailure("The restarted worker did not report expiry progress")
        if (
            self.sql(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE action = 'CHANGE_PROPOSAL_EXPIRED' "
                f"AND entity_id = '{proposal_id}';"
            )
            != "1"
        ):
            raise AcceptanceFailure("The proposal expiry did not create exactly one audit event")
        if (
            self.sql(
                "SELECT COUNT(*) FROM notifications "
                "WHERE event_type = 'CHANGE_PROPOSAL_EXPIRED' "
                f"AND appointment_id = UNHEX(REPLACE('{appointment_id}', '-', ''));"
            )
            != "2"
        ):
            raise AcceptanceFailure("The proposal expiry did not create exactly two notifications")

    def restart_persistence(self, worker_student_appointment: str) -> None:
        before = self.volume_before
        api_id, _ = self.require_worker_identity()
        require_success(
            self.compose("restart", "api", timeout=60),
            "The API container did not restart",
        )
        self.wait_gateway()
        self.require_worker_identity()
        self.wait_heartbeat()
        if (
            self.sql(
                "SELECT COUNT(*) FROM appointments "
                f"WHERE id = UNHEX(REPLACE('{worker_student_appointment}', '-', ''));"
            )
            != "1"
        ):
            raise AcceptanceFailure("The API restart did not preserve the worker test appointment")
        self.require_appointments_after_restart("CANCELLED")

        require_success(
            self.compose("restart", "mysql", timeout=90),
            "The test-owned MySQL container did not restart",
        )
        require_success(
            self.compose("up", "-d", "--wait", "--wait-timeout", "120", "mysql", timeout=150),
            "The test-owned MySQL container did not become healthy after restart",
        )
        after = one_volume(
            self.volume_name,
            {
                "com.docker.compose.project": self.project,
                "com.docker.compose.volume": "mysql_data",
            },
        )
        if before != after:
            raise AcceptanceFailure("The persistent MySQL volume identity changed during restart")
        self.wait_gateway()
        self.wait_heartbeat()
        current_api_id = require_success(
            self.compose("ps", "-q", "api"), "The API container is missing"
        )
        if current_api_id != api_id:
            raise AcceptanceFailure("The API container was recreated during a database restart")
        if (
            self.sql(
                "SELECT COUNT(*) FROM appointments "
                f"WHERE id = UNHEX(REPLACE('{worker_student_appointment}', '-', ''));"
            )
            != "1"
        ):
            raise AcceptanceFailure("The MySQL restart did not preserve appointment data")
        self.require_appointments_after_restart("CANCELLED")

    def incompatible_schema(self) -> None:
        if self.sql("SELECT version_num FROM alembic_version;") != SCHEMA_REVISION:
            raise AcceptanceFailure("The test-owned schema did not start at the accepted revision")
        self.sql("UPDATE alembic_version SET version_num = 'unsupported_task6_revision';")
        try:
            self.expect_compose_failure(
                "--profile", "operations", "run", "--rm", "-T", "migrate", timeout=180
            )
            self.expect_compose_failure("run", "--rm", "-T", "schema", timeout=60)
            response = self.http("GET", "/health/ready", timeout=5)
            if response.status_code != 503:
                raise AcceptanceFailure("An incompatible release unexpectedly reported ready")
        finally:
            self.sql(
                "UPDATE alembic_version "
                f"SET version_num = '{SCHEMA_REVISION}' "
                "WHERE version_num = 'unsupported_task6_revision';"
            )
        require_success(
            self.compose("run", "--rm", "-T", "schema", timeout=60),
            "Schema readiness did not recover after restoring the accepted revision",
        )
        require_success(
            self.compose("restart", "worker", timeout=60),
            "The worker did not recover after schema validation",
        )
        self.wait_gateway()
        self.wait_heartbeat()

    def database_outage(self) -> None:
        api_id = require_success(self.compose("ps", "-q", "api"), "The API container is missing")
        require_success(
            self.compose("stop", "--timeout", "10", "mysql", timeout=30),
            "The test-owned database did not stop for the outage check",
        )
        try:
            started = time.monotonic()
            ready = self.http("GET", "/health/ready", timeout=5)
            ready_elapsed = time.monotonic() - started
            if ready.status_code != 503 or ready_elapsed >= 5:
                raise AcceptanceFailure("Database outage readiness was not a bounded 503")
            started = time.monotonic()
            login = self.http(
                "POST",
                "/api/v1/auth/login",
                timeout=5,
                headers={"Origin": self.origin},
                json={"email": self.student_email, "password": self.student_password},
            )
            login_elapsed = time.monotonic() - started
            if login.status_code != 503 or login_elapsed >= 5:
                raise AcceptanceFailure("Database outage login was not a bounded 503")
            current_api_id = require_success(
                self.compose("ps", "-q", "api"), "The API container is missing"
            )
            if current_api_id != api_id:
                raise AcceptanceFailure("The API container exited during a bounded database outage")
        finally:
            require_success(
                self.compose("up", "-d", "--wait", "--wait-timeout", "120", "mysql", timeout=150),
                "The test-owned database did not recover from its bounded outage",
            )
        self.wait_gateway()
        self.wait_heartbeat()
        self.require_appointments_after_restart("CANCELLED")

    def assert_demo_untouched(self) -> None:
        if self.demo_snapshot is None:
            return
        if self._project_snapshot("prl-demo") != self.demo_snapshot:
            raise AcceptanceFailure("The preexisting prl-demo project changed during acceptance")

    def cleanup(self) -> None:
        # The project is generated above and checked before every Compose command.
        if self.project == "prl-demo" or not self.project.startswith(PROJECT_PREFIX):
            raise AcceptanceFailure("Refusing cleanup without an exact test-project identity")
        container_listing = invoke(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"]
        )
        if container_listing.returncode != 0:
            raise AcceptanceFailure("The test-owned containers could not be verified for cleanup")
        container_ids = list(filter(None, container_listing.stdout.splitlines()))
        if container_ids:
            containers = json_output(
                invoke(["docker", "inspect", *container_ids]),
                "The test-owned container labels could not be verified for cleanup",
            )
            if any(
                (container.get("Config") or {}).get("Labels", {}).get("com.docker.compose.project")
                != self.project
                for container in containers
            ):
                raise AcceptanceFailure("A container failed the exact test-project cleanup check")
        volume_listing = invoke(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ]
        )
        if volume_listing.returncode != 0:
            raise AcceptanceFailure("The test-owned volume could not be verified for cleanup")
        volumes = list(filter(None, volume_listing.stdout.splitlines()))
        if volumes and volumes != [self.volume_name]:
            raise AcceptanceFailure("The test project contains an unexpected volume")
        if volumes:
            one_volume(
                self.volume_name,
                {
                    "com.docker.compose.project": self.project,
                    "com.docker.compose.volume": "mysql_data",
                },
            )
        networks_result = invoke(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ]
        )
        if networks_result.returncode != 0:
            raise AcceptanceFailure("The test-owned networks could not be verified for cleanup")
        network_ids = list(filter(None, networks_result.stdout.splitlines()))
        if network_ids:
            networks = json_output(
                invoke(["docker", "network", "inspect", *network_ids]),
                "The test-owned network labels could not be verified for cleanup",
            )
            if any(
                (network.get("Labels") or {}).get("com.docker.compose.project") != self.project
                for network in networks
            ):
                raise AcceptanceFailure("A network failed the exact test-project cleanup check")
        if container_ids or network_ids:
            require_success(
                self.compose("down", "--remove-orphans", "--timeout", "30", timeout=60),
                "The test-owned Compose resources could not be stopped",
            )
        if volumes:
            require_success(
                invoke(["docker", "volume", "rm", self.volume_name]),
                "The verified test-owned database volume could not be removed",
            )
        for image in (f"prl-backend:{self.image_tag}", f"prl-frontend:{self.image_tag}"):
            inspected = invoke(["docker", "image", "inspect", image])
            if inspected.returncode == 0:
                require_success(
                    invoke(["docker", "image", "rm", image]),
                    "The test-owned image tag could not be removed",
                )
        self.assert_demo_untouched()


def test_compose_forwards_stdin_to_docker_command(monkeypatch: pytest.MonkeyPatch) -> None:
    stack = ComposeAcceptance.__new__(ComposeAcceptance)
    stack.project = "prl-task6-unit"
    stack.base = ["docker", "compose", "--project-name", stack.project]
    stack.env = {}
    statement = "SELECT 1;"
    calls: list[tuple[list[str], str | None]] = []

    def fake_invoke(
        args: list[str],
        *,
        cwd: Path = ROOT,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float = 180,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, input_text))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setitem(globals(), "invoke", fake_invoke)
    result = stack.compose("exec", "-T", "mysql", "mysql", input_text=statement)

    assert result.returncode == 0
    assert calls == [
        (
            [
                "docker",
                "compose",
                "--project-name",
                stack.project,
                "exec",
                "-T",
                "mysql",
                "mysql",
            ],
            statement,
        )
    ]


def test_playwright_runner_does_not_filter_required_role_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = ComposeAcceptance.__new__(ComposeAcceptance)
    stack.env = {"PRL_COMPOSE_PLAYWRIGHT_GREP": "student only"}
    stack.origin = "http://localhost:3100"
    stack.student_email = "student@example.invalid"
    stack.student_password = "test-password"
    stack.staff_email = "staff@example.invalid"
    stack.staff_password = "test-password"
    calls: list[tuple[list[str], float]] = []

    def fake_invoke(
        args: list[str],
        *,
        cwd: Path = ROOT,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float = 180,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, "2 passed", "")

    monkeypatch.setattr(shutil, "which", lambda name: "node")
    monkeypatch.setitem(globals(), "invoke", fake_invoke)
    stack.run_playwright("appointment-id", "Staff Name", "2026-10-25")

    assert calls == [
        (
            [
                "node",
                str(ROOT / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                str(PLAYWRIGHT_CONFIG),
            ],
            240,
        )
    ]


@pytest.mark.skipif(not RUN, reason="opt-in isolated local Compose acceptance")
def test_real_compose_smoke_and_lifecycle_acceptance(tmp_path: Path) -> None:
    stack = ComposeAcceptance(tmp_path)
    try:
        stack.start()
        worker_appointment, proposal_id, staff_name, booking_date = stack.create_worker_case()
        stack.run_playwright(worker_appointment, staff_name, booking_date)
        stack.expire_worker_case(worker_appointment, proposal_id)
        stack.restart_persistence(worker_appointment)
        stack.incompatible_schema()
        stack.database_outage()
        stack.assert_demo_untouched()
    finally:
        stack.cleanup()
