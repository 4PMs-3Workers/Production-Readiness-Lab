import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from sqlalchemy.engine import make_url
from tests.containers import test_backup_monitoring_stack as backup_stack
from tests.containers.test_telemetry_stack import FullStackRuntime
from tools.backup.recovery_config import main as render_gateway
from tools.backup.recovery_config import render

ROOT = Path(__file__).resolve().parents[3]


def test_restore_helper_uses_running_stack_and_granted_identity(tmp_path, monkeypatch):
    base = ["docker", "compose", "--project-name", "owned-test"]
    for name in ("compose.yaml", "compose.https.yaml", "compose.observability.yaml"):
        base.extend(["-f", str(ROOT / "tools/deploy" / name)])
    runtime = cast(
        FullStackRuntime,
        SimpleNamespace(
            backup_password="backup-password",
            backup_env_file=tmp_path / "backup.env",
            backup_env_text="DATABASE_URL=mysql+asyncmy://prl_backup:backup-password@mysql/prl\n",
            stack=SimpleNamespace(base=base, env={}, image_tag="owned", expected_sha="a" * 40),
            compose=lambda *args: subprocess.CompletedProcess(args, 0, "mysql-id", ""),
        ),
    )
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = "sha256:" + "b" * 64 if args[:3] == ["docker", "image", "inspect"] else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(backup_stack.subprocess, "run", run)
    result = backup_stack._run_restore(runtime, "backup-id")

    assert result.returncode == 0
    restore_args, restore_options = calls[-1]
    assert restore_args[: len(base)] == base
    assert restore_args[len(base) : len(base) + 2] == [
        "-f",
        str(ROOT / "tools/deploy/compose.backup.yaml"),
    ]
    target = make_url(runtime.backup_env_file.read_text().split("RESTORE_DATABASE_URL=")[1].strip())
    assert (target.username, target.password, target.database) == (
        "prl_backup",
        runtime.backup_password,
        "prlrestore",
    )
    assert "TO 'prl_backup'@'%'" in calls[0][0][-1]
    assert "ON prlrestore.*" in calls[0][0][-1]
    assert restore_args[-2:] == ["--target-database", "prlrestore"]
    assert restore_options["env"]["PRL_BACKEND_IMAGE_ID"] == "sha256:" + "b" * 64


def test_recovery_gateway_rewrites_routes_and_observability_identity_together(tmp_path):
    config = render(tmp_path)
    for name in ("PRL_RECOVERY_NGINX_CONF", "PRL_RECOVERY_NGINX_ROUTES"):
        contents = Path(config[name]).read_text()
        assert "172.30.34.3:8000" not in contents
        assert "172.30.36.3:8000" in contents
    assert config["PRL_RECOVERY_GATEWAY_IP"] + "/32" == "172.30.36.2/32"


def test_offhost_gateway_renders_from_retained_templates_without_checkout_fallback(
    tmp_path, capsys
):
    retained = tmp_path / "verified-configuration" / "nginx"
    retained.mkdir(parents=True)
    for name in ("nginx.conf", "routes.conf"):
        (retained / name).write_text(
            f"# retained {name}\nproxy_pass http://172.30.34.3:8000;\n", encoding="utf-8"
        )
    output = tmp_path / "recovered"
    args = ["--directory", str(output), "--template-directory", str(retained)]
    assert render_gateway(args) == 0
    config = json.loads(capsys.readouterr().out)
    for name, variable in (
        ("nginx.conf", "PRL_RECOVERY_NGINX_CONF"),
        ("routes.conf", "PRL_RECOVERY_NGINX_ROUTES"),
    ):
        assert Path(config[variable]).read_text() == (
            f"# retained {name}\nproxy_pass http://172.30.36.3:8000;\n"
        )
        assert "172.30.34.3:8000" in (retained / name).read_text()

    (retained / "routes.conf").unlink()
    output = tmp_path / "missing-template"
    assert render_gateway(["--directory", str(output), *args[2:]]) == 1
    assert json.loads(capsys.readouterr().out)["success"] is False
    assert not output.exists()


@pytest.mark.parametrize("subnet", ["172.30.34.0/29", "0.0.0.0/0", "8.8.8.0/29", "::/0"])
def test_recovery_refuses_source_or_unbounded_networks(tmp_path, subnet):
    with pytest.raises(ValueError):
        render(tmp_path, subnet)


@pytest.mark.parametrize("mode", ["local-recovery", "https-monitoring"])
def test_backup_and_recovery_compose_config_are_valid_without_resolving_secrets(tmp_path, mode):
    environment = dict(os.environ, **render(tmp_path / "gateway"))
    for kind in ("DB", "RUNTIME", "MIGRATION", "SEED", "BACKUP"):
        path = tmp_path / (kind.lower() + ".env")
        path.write_text("DATABASE_URL=mysql+asyncmy://private:private@mysql/prl\n")
        environment[f"PRL_{kind}_ENV_FILE"] = str(path)
    environment.update(
        PRL_SOURCE_SHA="a" * 40,
        PRL_BACKEND_IMAGE_ID="sha256:" + "b" * 64,
        PRL_BACKUP_VOLUME="prl-backup-config-test",
    )
    if mode == "https-monitoring":
        external_file = tmp_path / "external-secret"
        external_file.write_text("private")
        for key in (
            "PRL_TLS_CERT_FILE",
            "PRL_TLS_KEY_FILE",
            "PRL_OPS_TLS_CERT_FILE",
            "PRL_OPS_TLS_KEY_FILE",
            "PRL_GRAFANA_ADMIN_USER_FILE",
            "PRL_GRAFANA_ADMIN_PASSWORD_FILE",
        ):
            environment[key] = str(external_file)
        environment.update(
            SERVICE_RELEASE="a" * 40,
            PRL_HTTPS_ORIGIN="https://app.example.invalid",
            PRL_OPS_HOST="ops.example.invalid",
            PRL_OPS_ORIGIN="https://ops.example.invalid",
            PRL_OPS_CIDRS="127.0.0.1/32",
        )
    overlays = (
        ("compose.local.yaml", "compose.backup.yaml", "compose.recovery.yaml")
        if mode == "local-recovery"
        else ("compose.https.yaml", "compose.observability.yaml", "compose.backup.yaml")
    )
    command = ["docker", "compose", "--project-name", "prl-recovery-config-test"]
    for filename in ("compose.yaml", *overlays):
        command.extend(["-f", str(ROOT / "tools/deploy" / filename)])
    # Parsing does not start services or require daemon access. Missing Docker is
    # a failed prerequisite, never a silently skipped required restore test.
    result = subprocess.run(
        command + ["--profile", "backup", "config", "--quiet"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, "The backup Compose configuration could not be rendered"
    assert "private" not in result.stdout


def test_backup_exporter_compose_contract_is_internal_and_read_only():
    compose = yaml.safe_load((ROOT / "tools/deploy/compose.observability.yaml").read_text())
    service = compose["services"]["backup_metrics_exporter"]
    volume = service["volumes"][0]

    assert service["image"] == "prl-backend:${PRL_IMAGE_TAG:-local}"
    assert service["command"] == ["python", "-m", "tools.backup.metrics_exporter"]
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["networks"] == ["telemetry"]
    assert "ports" not in service and "env_file" not in service
    assert volume == {
        "type": "volume",
        "source": "backup_storage",
        "target": "/backups",
        "read_only": True,
    }
    assert service["mem_limit"] == "128m"
    assert service["cpus"] == 0.5
    assert service["pids_limit"] == 64
    assert compose["networks"]["telemetry"]["internal"] is True
    assert compose["volumes"]["backup_storage"]["external"] is True
    assert (
        "COPY tools/backup/metrics_exporter.py ./tools/backup/metrics_exporter.py"
        in (ROOT / "Dockerfile.backend").read_text()
    )


def test_prometheus_scrapes_internal_backup_exporter():
    config = yaml.safe_load((ROOT / "tools/infra/prometheus.yml").read_text())
    job = next(job for job in config["scrape_configs"] if job["job_name"] == "backup-monitor")

    assert job["metrics_path"] == "/metrics"
    assert job["static_configs"][0]["targets"] == ["backup_metrics_exporter:9108"]
