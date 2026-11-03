import os
import subprocess
from pathlib import Path

import pytest
import yaml
from tools.backup.recovery_config import render

ROOT = Path(__file__).resolve().parents[3]


def test_recovery_gateway_rewrites_routes_and_observability_identity_together(tmp_path):
    config = render(tmp_path)
    for name in ("PRL_RECOVERY_NGINX_CONF", "PRL_RECOVERY_NGINX_ROUTES"):
        contents = Path(config[name]).read_text()
        assert "172.30.34.3:8000" not in contents
        assert "172.30.36.3:8000" in contents
    assert config["PRL_RECOVERY_GATEWAY_IP"] + "/32" == "172.30.36.2/32"


@pytest.mark.parametrize("subnet", ["172.30.34.0/29", "0.0.0.0/0", "8.8.8.0/29", "::/0"])
def test_recovery_refuses_source_or_unbounded_networks(tmp_path, subnet):
    with pytest.raises(ValueError):
        render(tmp_path, subnet)


def test_backup_and_recovery_compose_config_are_valid_without_resolving_secrets(tmp_path):
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
    command = ["docker", "compose", "--project-name", "prl-recovery-config-test"]
    for filename in (
        "compose.yaml",
        "compose.local.yaml",
        "compose.backup.yaml",
        "compose.recovery.yaml",
    ):
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
    assert result.returncode == 0
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
