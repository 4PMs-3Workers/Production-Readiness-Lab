"""Real Compose configuration and isolated MySQL 8.4 lifecycle assertions.

Opt in: RUN_COMPOSE_TESTS=1 python -m pytest tests/containers/test_compose_core.py -q
Creates only a UUID-named local project; never uses the application E2E reset fixture.
"""

import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "tools/deploy/compose.yaml"
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_COMPOSE_TESTS") != "1", reason="opt-in local Docker acceptance"
)


def command(args, *, env=None, input=None, timeout=180):
    return subprocess.run(
        args, cwd=ROOT, env=env, input=input, text=True, capture_output=True, timeout=timeout
    )


def seconds(duration):
    units = {"h": 3600, "m": 60, "s": 1}
    return sum(
        float(value) * units[unit] for value, unit in re.findall(r"([0-9.]+)([hms])", duration)
    )


@pytest.fixture
def stack(tmp_path):
    project = "prl-task3-" + uuid4().hex
    passwords = {
        name: secrets.token_hex(24) + "'\\$" for name in ("root", "migration", "runtime", "seed")
    }
    # The upstream MySQL root bootstrap embeds its password in SQL; use random hex.
    passwords["root"] = secrets.token_hex(32)
    encoded = {name: quote(value, safe="") for name, value in passwords.items()}
    paths = {}
    values = {
        "db": {
            "MYSQL_ROOT_PASSWORD": passwords["root"],
            "PRL_MIGRATION_PASSWORD": passwords["migration"],
            "PRL_RUNTIME_PASSWORD": passwords["runtime"],
            "PRL_SEED_PASSWORD": passwords["seed"],
        },
        "runtime": {
            "DATABASE_URL": f"mysql+asyncmy://prl_runtime:{encoded['runtime']}@mysql/prl",
            "APPLICATION_ORIGIN": "http://localhost:8080",
            "COOKIE_SECRET": secrets.token_hex(32),
            "CSRF_SECRET": secrets.token_hex(32),
            "ENVIRONMENT": "local",
            "COOKIE_SECURE": "false",
        },
        "migration": {
            "DATABASE_URL": f"mysql+asyncmy://prl_migration:{encoded['migration']}@mysql/prl"
        },
        "seed": {
            "DATABASE_URL": f"mysql+asyncmy://prl_seed:{encoded['seed']}@mysql/prl",
            "ENVIRONMENT": "local",
            "DEMO_SEED_DATABASE": "prl",
            **{
                f"DEMO_{role}_PASSWORD": secrets.token_hex(24)
                for role in ("ADMIN", "STAFF", "STUDENT")
            },
        },
    }
    for name, entries in values.items():
        path = tmp_path / (name + ".env")
        path.write_text("".join(f"{key}={value}\n" for key, value in entries.items()))
        paths[f"PRL_{name.upper()}_ENV_FILE"] = str(path)
    env = dict(os.environ, **paths, PRL_IMAGE_TAG="task3-test")
    base = ["docker", "compose", "-p", project, "-f", str(COMPOSE)]

    def compose(*args, input=None, timeout=180):
        result = command(base + list(args), env=env, input=input, timeout=timeout)
        for entries in values.values():
            for key, value in entries.items():
                if "PASSWORD" in key or "SECRET" in key or key == "DATABASE_URL":
                    result.stdout = result.stdout.replace(value, "[redacted]")
                    result.stderr = result.stderr.replace(value, "[redacted]")
        return result

    def sql(principal, statement):
        password_var = (
            "MYSQL_ROOT_PASSWORD" if principal == "root" else f"PRL_{principal.upper()}_PASSWORD"
        )
        username = "root" if principal == "root" else "prl_" + principal
        return compose(
            "exec",
            "-T",
            "mysql",
            "bash",
            "-c",
            (
                f'MYSQL_PWD="${{{password_var}}}" mysql --protocol=socket -u{username} '
                "--batch --skip-column-names prl"
            ),
            input=statement,
        )

    try:
        yield compose, sql
    finally:
        # This fixture owns only its UUID project. Production/down never deletes volumes.
        assert project.startswith("prl-task3-") and len(project) == 42
        result = compose("down", "--remove-orphans", "--timeout", "20")
        assert result.returncode == 0, "Could not stop test-owned Compose project"
        result = command(["docker", "volume", "rm", project + "_mysql_data"])
        assert result.returncode == 0 or "no such volume" in result.stderr.lower()


def test_compose_configuration_keeps_secrets_ports_and_ddl_out_of_runtime(stack):
    compose, _ = stack
    result = compose(
        "--profile",
        "operations",
        "--profile",
        "seed",
        "config",
        "--no-env-resolution",
        "--format",
        "json",
    )
    assert result.returncode == 0, "Compose configuration is invalid"
    config = json.loads(result.stdout)
    services = config["services"]
    source_services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert set(services) == {"mysql", "frontend", "api", "worker", "schema", "migrate", "seed"}
    assert all(not service.get("ports") for service in services.values())
    assert all(network["internal"] for network in config["networks"].values())
    for service in services.values():
        assert int(service["mem_limit"]) > 0
        assert float(service["cpus"]) > 0
        assert int(service["pids_limit"]) > 0
        assert seconds(service["stop_grace_period"]) > 0
        if "healthcheck" in service:
            probe = service["healthcheck"]
            assert 0 < seconds(probe["timeout"]) <= 5
            assert 0 < probe["retries"] <= 24
    assert services["mysql"]["image"].startswith("mysql:8.4.")
    assert services["mysql"]["volumes"][0]["type"] == "volume"
    assert services["mysql"]["volumes"][0]["target"] == "/var/lib/mysql"
    for name in ("api", "worker", "schema"):
        service = services[name]
        assert service["image"] == services["migrate"]["image"]
        assert service["build"] == services["migrate"]["build"]
        assert source_services[name]["env_file"] == [
            {
                "path": "${PRL_RUNTIME_ENV_FILE:?Set external runtime env-file path}",
                "format": "raw",
            }
        ]
        assert service["environment"] == {
            "ACCEPTED_SCHEMA_REVISIONS": "20261019_1200_proposal_replay"
        }
    for name in ("api", "worker"):
        assert (
            services[name]["depends_on"]["schema"]["condition"] == "service_completed_successfully"
        )
        assert services[name]["restart"] == "on-failure:3"
    assert services["api"].get("command") is None
    assert services["worker"]["command"] == ["python", "-m", "app.worker"]
    assert services["seed"]["profiles"] == ["seed"]
    assert services["migrate"]["profiles"] == ["operations"]
    assert services["migrate"]["restart"] == "no"


def test_mysql_migration_grants_schema_gate_and_persistence(stack):
    compose, sql = stack
    assert compose("build", "api", "frontend", timeout=600).returncode == 0, "Image build failed"
    assert compose("up", "-d", "--wait", "mysql", timeout=180).returncode == 0, compose(
        "logs", "--no-color", "mysql"
    ).stdout
    # No schema or business data is created by ordinary database startup.
    assert sql("root", "SHOW TABLES;").stdout.strip() == ""
    assert compose("run", "--rm", "-T", "schema").returncode != 0
    assert compose("run", "--rm", "-T", "migrate").returncode == 0, "Explicit migration failed"
    # A grant failure midway through provisioning must leave the release unready.
    assert sql("root", "RENAME TABLE service_hours TO missing_service_hours;").returncode == 0
    assert compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh").returncode != 0
    assert compose("run", "--rm", "-T", "schema").returncode != 0
    assert sql("root", "RENAME TABLE missing_service_hours TO service_hours;").returncode == 0
    # A migrated schema alone is insufficient: no runtime grants means no API startup.
    assert compose("up", "-d", "api").returncode != 0
    assert (
        compose("exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh").returncode == 0
    ), "Grant provisioning failed"
    assert compose("run", "--rm", "-T", "schema").returncode == 0
    assert (
        sql("runtime", "SELECT version_num FROM alembic_version;").stdout.strip()
        == "20261019_1200_proposal_replay"
    )
    assert (
        sql("runtime", "INSERT INTO audit_events SELECT * FROM audit_events WHERE 1=0;").returncode
        == 0
    )
    for statement in (
        "UPDATE audit_events SET request_id='denied' WHERE 1=0;",
        "DELETE FROM audit_events WHERE 1=0;",
        "CREATE TABLE forbidden (id INT);",
        "UPDATE alembic_version SET version_num='denied';",
    ):
        assert sql("runtime", statement).returncode != 0, "Runtime privilege boundary failed"
    assert sql("seed", "SELECT * FROM appointments LIMIT 0;").returncode != 0
    assert sql("seed", "UPDATE users SET name='denied' WHERE 1=0;").returncode != 0
    assert compose("run", "--rm", "-T", "seed").returncode == 0, "Reviewed seed failed"
    count = sql("runtime", "SELECT COUNT(*) FROM users;").stdout.strip()
    assert int(count) > 0
    assert compose("run", "--rm", "-T", "seed").returncode == 0, "Seed must be idempotent"
    assert (
        compose("up", "-d", "--wait", "api", "worker", "frontend", timeout=120).returncode == 0
    ), compose("logs", "--no-color", "api", "frontend", "worker").stdout
    # Inspect actual container image/user identity rather than YAML text alone.
    containers = {name: compose("ps", "-q", name).stdout.strip() for name in ("api", "worker")}
    inspections = [
        json.loads(command(["docker", "inspect", identifier]).stdout)[0]
        for identifier in containers.values()
    ]
    assert inspections[0]["Image"] == inspections[1]["Image"]
    assert all(item["Config"]["User"] == "10001:10001" for item in inspections)
    for item in inspections:
        entries = dict(value.split("=", 1) for value in item["Config"]["Env"])
        username = urlsplit(entries["DATABASE_URL"]).username
        assert username == "prl_runtime"
        assert not any("MIGRATION" in key or "ROOT_PASSWORD" in key for key in entries)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        logs = compose("logs", "--no-color", "worker").stdout
        if '"heartbeat"' in logs:
            break
        time.sleep(1)
    else:
        pytest.fail("Worker produced no live heartbeat")
    assert compose("restart", "mysql").returncode == 0
    assert compose("up", "-d", "--wait", "mysql", timeout=120).returncode == 0
    assert sql("runtime", "SELECT COUNT(*) FROM users;").stdout.strip() == count
    assert sql("root", "UPDATE alembic_version SET version_num='incompatible';").returncode == 0
    assert compose("run", "--rm", "-T", "schema").returncode != 0
    assert (
        sql(
            "root", "UPDATE alembic_version SET version_num='20261019_1200_proposal_replay';"
        ).returncode
        == 0
    )
    assert compose("run", "--rm", "-T", "schema").returncode == 0

    assert compose("down", "--timeout", "40").returncode == 0
    assert compose("up", "-d", "--wait", "mysql", timeout=120).returncode == 0
    assert sql("runtime", "SELECT COUNT(*) FROM users;").stdout.strip() == count
