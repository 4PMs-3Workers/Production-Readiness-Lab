"""Repeatable, non-publishing Task 6 config and unit validation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tools/deploy"
INFRA = ROOT / "tools/infra"
EVIDENCE = ROOT / ".superpowers/sdd/2026-10-23-observability-foundation"
LOCK_PATH = INFRA / "images.lock.json"


class CheckFailure(RuntimeError):
    pass


def _command_text(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else " ".join(arguments)


def _record(
    run_id: str,
    label: str,
    command: list[str],
    timeout: int = 180,
    env: dict[str, str] | None = None,
):
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CheckFailure(f"{label} could not complete within its bounded run") from None
    safe_label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")
    artifact = EVIDENCE / f"task-6-check-{run_id}-{safe_label}.txt"
    artifact.write_text(
        f"command: {_command_text(command)}\n"
        f"exit_code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n",
        encoding="utf-8",
    )
    if result.returncode:
        raise CheckFailure(f"{label} failed (exit {result.returncode}); see {artifact.name}")
    print(f"PASS {label} (evidence {artifact.name})", flush=True)
    return result


def _docker_run(
    image: str,
    *,
    entrypoint: str,
    args: list[str],
    memory: str,
    cpus: str,
    pids: str,
    extra: list[str] | None = None,
    timeout: int = 90,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--pull=never",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        cpus,
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--pids-limit",
        pids,
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777",
    ]
    if extra:
        command.extend(extra)
    command.extend(["--entrypoint", entrypoint, image, *args])
    return command


def _compose_environment(temp: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith(("PRL_", "COMPOSE_")) or name in {
            "SERVICE_RELEASE",
            "APPLICATION_ORIGIN",
            "DATABASE_URL",
            "ENVIRONMENT",
        }:
            environment.pop(name, None)
    files = {}
    for name in (
        "db",
        "runtime",
        "migration",
        "seed",
        "app-cert",
        "app-key",
        "ops-cert",
        "ops-key",
        "grafana-user",
        "grafana-password",
    ):
        path = temp / name
        path.write_text("", encoding="utf-8")
        files[name] = str(path.resolve())
    environment.update(
        PRL_DB_ENV_FILE=files["db"],
        PRL_RUNTIME_ENV_FILE=files["runtime"],
        PRL_MIGRATION_ENV_FILE=files["migration"],
        PRL_SEED_ENV_FILE=files["seed"],
        PRL_TLS_CERT_FILE=files["app-cert"],
        PRL_TLS_KEY_FILE=files["app-key"],
        PRL_OPS_TLS_CERT_FILE=files["ops-cert"],
        PRL_OPS_TLS_KEY_FILE=files["ops-key"],
        PRL_GRAFANA_ADMIN_USER_FILE=files["grafana-user"],
        PRL_GRAFANA_ADMIN_PASSWORD_FILE=files["grafana-password"],
        PRL_HTTPS_ORIGIN="https://app.example.invalid:18443",
        PRL_HTTPS_BIND="127.0.0.1",
        PRL_HTTPS_PORT="18443",
        PRL_OPS_ORIGIN="https://ops.example.invalid:18443",
        PRL_OPS_HOST="ops.example.invalid",
        PRL_OPS_CIDRS="127.0.0.1/32",
        PRL_EXPECTED_WORKER_SLOTS="1",
        PRL_IMAGE_TAG="check-only",
        SERVICE_RELEASE="a" * 40,
    )
    return environment


def _compose_project_name(run_id: str) -> str:
    return f"prl-task6-check-{run_id.lower()}"


def _validate_locked_images(services: dict[str, object], lock_images: dict[str, str]) -> None:
    for name, reference in lock_images.items():
        service_name = name
        if name == "gateway":
            actual = services.get("gateway", {}).get("image")
            helper_actual = services.get("gateway_log_rotate", {}).get("image")
            if actual != reference or helper_actual != reference:
                raise CheckFailure("The gateway or rotation helper differs from its exact lock")
            continue
        if name == "collector":
            service = services.get("collector", {})
            build = service.get("build", {})
            source_dockerfile = INFRA / "collector.Dockerfile"
            first_line = next(
                (
                    line.strip()
                    for line in source_dockerfile.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ),
                "",
            )
            dockerfile_lines = source_dockerfile.read_text(encoding="utf-8").splitlines()
            expected_storage_copy = (
                "COPY --chown=10001:10001 tools/infra/collector-file-storage/ "
                "/var/lib/otelcol/file_storage/"
            )
            deployed_sha = services.get("api", {}).get("environment", {}).get("SERVICE_RELEASE")
            if (
                build.get("dockerfile") != "tools/infra/collector.Dockerfile"
                or first_line != f"FROM {reference}"
                or build.get("args", {}).get("PRL_SOURCE_SHA") != deployed_sha
                or expected_storage_copy not in dockerfile_lines
                or not (INFRA / "collector-file-storage" / ".keep").is_file()
            ):
                raise CheckFailure(
                    "Collector build must use the locked base, an owned storage context, "
                    "and the deployed source SHA"
                )
            continue
        actual = services.get(service_name, {}).get("image")
        if actual != reference:
            raise CheckFailure(f"Compose image for {name} differs from its exact lock")


def _run_compose(run_id: str, env: dict[str, str]) -> None:
    project = _compose_project_name(run_id)
    base = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(DEPLOY / "compose.yaml"),
        "-f",
        str(DEPLOY / "compose.https.yaml"),
        "-f",
        str(DEPLOY / "compose.observability.yaml"),
    ]
    _record(
        run_id,
        "Compose resolved configuration",
        base + ["config", "--quiet"],
        timeout=60,
        env=env,
    )
    result = _record(
        run_id,
        "Compose service inventory",
        base + ["config", "--no-env-resolution", "--format", "json"],
        timeout=60,
        env=env,
    )
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise CheckFailure("Compose service inventory was not valid JSON") from None
    services = config.get("services", {})
    published = {name for name, service in services.items() if service.get("ports")}
    if published != {"gateway"}:
        raise CheckFailure("Compose observability mode publishes a non-gateway service")
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    _validate_locked_images(services, lock["images"])
    print("PASS Compose telemetry ports and exact image references", flush=True)


def _run_pinned_validators(run_id: str, lock: dict[str, str]) -> None:
    def mount(source: Path, target: str) -> list[str]:
        return ["--mount", f"type=bind,source={source.resolve()},target={target},readonly"]

    collector = lock["collector"]
    collector_args = _docker_run(
        collector,
        entrypoint="/otelcol-contrib",
        args=["validate", "--config=/etc/otelcol-contrib/config.yaml"],
        memory="512m",
        cpus="1.0",
        pids="128",
        extra=[
            "--user",
            "10001:10001",
            "--tmpfs",
            "/var/lib/otelcol/file_storage:rw,nosuid,nodev,noexec,size=16m,uid=10001,gid=10001,mode=0700",
            *mount(INFRA / "otel-collector.yaml", "/etc/otelcol-contrib/config.yaml"),
        ],
    )
    _record(run_id, "Collector 0.163.2 config validation", collector_args, timeout=60)

    promtool = _docker_run(
        lock["prometheus"],
        entrypoint="/bin/promtool",
        args=["check", "config", "/etc/prometheus/prometheus.yml"],
        memory="512m",
        cpus="1.0",
        pids="128",
        extra=[
            *mount(INFRA / "prometheus.yml", "/etc/prometheus/prometheus.yml"),
            *mount(
                INFRA / "prometheus-rules.yml", "/etc/prometheus/rules/telemetry-foundation.yml"
            ),
        ],
    )
    _record(run_id, "Prometheus 3.15.1 config validation", promtool)
    rules = _docker_run(
        lock["prometheus"],
        entrypoint="/bin/promtool",
        args=["check", "rules", "/etc/prometheus/rules/telemetry-foundation.yml"],
        memory="512m",
        cpus="1.0",
        pids="128",
        extra=[
            *mount(INFRA / "prometheus-rules.yml", "/etc/prometheus/rules/telemetry-foundation.yml")
        ],
    )
    _record(run_id, "Prometheus 3.15.1 rules validation", rules)

    rule_tests = _docker_run(
        lock["prometheus"],
        entrypoint="/bin/promtool",
        args=["test", "rules", "/etc/prometheus/rules/prometheus-rule-tests.yml"],
        memory="512m",
        cpus="1.0",
        pids="128",
        extra=[
            "--workdir",
            "/etc/prometheus/rules",
            *mount(
                INFRA / "prometheus-rules.yml",
                "/etc/prometheus/rules/prometheus-rules.yml",
            ),
            *mount(
                INFRA / "prometheus-rule-tests.yml",
                "/etc/prometheus/rules/prometheus-rule-tests.yml",
            ),
        ],
        timeout=120,
    )
    _record(run_id, "Prometheus 3.15.1 rule tests", rule_tests, timeout=120)

    loki = _docker_run(
        lock["loki"],
        entrypoint="/usr/bin/loki",
        args=["-config.file=/etc/loki/loki.yaml", "-verify-config=true"],
        memory="768m",
        cpus="1.0",
        pids="128",
        extra=[*mount(INFRA / "loki.yaml", "/etc/loki/loki.yaml")],
        timeout=90,
    )
    _record(run_id, "Loki 3.7.9 config validation", loki, timeout=100)

    tempo = _docker_run(
        lock["tempo"],
        entrypoint="/tempo",
        args=[
            "-config.file=/etc/tempo/tempo.yaml",
            "-config.verify=true",
            "-config.verify-errors-only=true",
        ],
        memory="512m",
        cpus="1.0",
        pids="128",
        extra=[*mount(INFRA / "tempo.yaml", "/etc/tempo/tempo.yaml")],
        timeout=90,
    )
    _record(run_id, "Tempo 3.1.1 strict config verification", tempo, timeout=100)

    alertmanager = _docker_run(
        lock["alertmanager"],
        entrypoint="/bin/amtool",
        args=["check-config", "/etc/alertmanager/alertmanager.yml"],
        memory="128m",
        cpus="0.5",
        pids="64",
        extra=[*mount(INFRA / "alertmanager.yml", "/etc/alertmanager/alertmanager.yml")],
    )
    _record(run_id, "Alertmanager 0.34.1 config validation", alertmanager)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Validate Task 6 pinned images, configs, Compose and unit contracts.")
    )
    parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    try:
        lock_document = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
        images = lock_document["images"]
        expected = {
            "collector",
            "prometheus",
            "loki",
            "tempo",
            "grafana",
            "alertmanager",
            "mysql",
            "gateway",
        }
        if lock_document.get("schema_version") != 1 or set(images) != expected:
            raise CheckFailure(
                "The locked vendor image inventory is incomplete or has unexpected entries"
            )
        for name, reference in images.items():
            if "@sha256:" not in reference:
                raise CheckFailure(f"The image lock for {name} is not immutable")
            inspected = _record(
                run_id,
                f"Cached exact image {name}",
                ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
                timeout=30,
            )
            if not re.fullmatch(r"sha256:[0-9a-f]{64}\s*", inspected.stdout):
                raise CheckFailure(f"The cached exact image identity for {name} is invalid")

        _run_pinned_validators(run_id, images)
        with tempfile.TemporaryDirectory(prefix="prl-task6-compose-") as directory:
            temp = Path(directory)
            env = _compose_environment(temp)
            _run_compose(run_id, env)

        tests = sorted((ROOT / "tests/backend/unit").glob("test_task6_*.py"))
        if not tests:
            raise CheckFailure("Task 6 unit contracts were not found")
        _record(
            run_id,
            "Task 6 unit contracts",
            [sys.executable, "-m", "pytest", "-q", "--tb=short", *map(str, tests)],
            timeout=180,
        )
        print(
            "Task 6 static validation passed; full runtime acceptance "
            "runs in test_telemetry_stack.py."
        )
        return 0
    except CheckFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
