"""Required end-to-end checks for backup health metrics and alert delivery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from tests.containers.test_compose_acceptance import (
    AcceptanceFailure,
)
from tests.containers.test_compose_acceptance import (
    require_success as acceptance_success,
)
from tests.containers.test_telemetry_stack import FullStackRuntime

ROOT = Path(__file__).resolve().parents[2]


def _run_backup(runtime: FullStackRuntime, *, initialize: bool = False):
    environment = dict(os.environ)
    environment.update(runtime.stack.env)
    environment.update(
        PRL_SOURCE_SHA=runtime.stack.expected_sha,
        PRL_IMAGE_TAG=runtime.stack.image_tag,
        PRL_BACKUP_JOB_SECONDS="600",
    )
    arguments = [sys.executable, str(ROOT / "tools/backup/run.py")]
    if initialize:
        arguments.append("--initialize-monitor-state")
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=750,
        check=False,
    )


def _wait_for(predicate, description: str, timeout: float = 90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(2)
    raise AcceptanceFailure(f"Timed out waiting for {description}")


def _metric(runtime: FullStackRuntime, name: str) -> float | None:
    response = runtime.api_json(
        "http://prometheus:9090/api/v1/query?"
        + urlencode({"query": f'{name}{{environment="test",store="backup-store"}}'})
    )
    if response.get("status") != "success":
        raise AcceptanceFailure("Prometheus returned an invalid backup metric query")
    result = response.get("data", {}).get("result", [])
    if not isinstance(result, list) or len(result) > 1:
        raise AcceptanceFailure("Prometheus returned an ambiguous backup metric vector")
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, TypeError, ValueError, IndexError):
        raise AcceptanceFailure("Prometheus returned an invalid backup metric sample") from None


def _run_restore(runtime: FullStackRuntime, backup_id: str) -> subprocess.CompletedProcess[str]:
    password = runtime.backup_password
    runtime.backup_env_file.write_text(
        runtime.backup_env_text
        + "RESTORE_DATABASE_URL=mysql+asyncmy://root:"
        + quote(password, safe="")
        + "@mysql/prl_restore\n",
        encoding="utf-8",
    )
    runtime.backup_env_file.chmod(0o600)
    mysql = acceptance_success(
        runtime.compose("ps", "-q", "mysql"), "The owned MySQL container is missing"
    )
    acceptance_success(
        subprocess.run(
            [
                "docker",
                "exec",
                mysql,
                "sh",
                "-ec",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot -e '
                '"CREATE DATABASE prl_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; '
                "GRANT ALL PRIVILEGES ON prl_restore.* TO 'prl_backup'@'%'\"",
            ],
            text=True,
            capture_output=True,
            check=False,
        ),
        "The isolated restore database could not be created",
    )
    image = acceptance_success(
        subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                f"prl-backend:{runtime.stack.image_tag}",
            ],
            text=True,
            capture_output=True,
            check=False,
        ),
        "The exact-source backend image is missing",
    )
    environment = dict(os.environ)
    environment.update(runtime.stack.env)
    environment.update(
        PRL_SOURCE_SHA=runtime.stack.expected_sha,
        PRL_IMAGE_TAG=runtime.stack.image_tag,
        PRL_BACKEND_IMAGE_ID=image,
    )
    return subprocess.run(
        [
            *runtime.stack.base,
            "-f",
            str(ROOT / "tools/deploy/compose.local.yaml"),
            "-f",
            str(ROOT / "tools/deploy/compose.backup.yaml"),
            "--profile",
            "backup",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "backup",
            "restore",
            "--backup-id",
            backup_id,
            "--target-database",
            "prl_restore",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=750,
        check=False,
    )


def test_scheduled_backup_success_and_failure_update_exported_metrics_and_receiver_alerts(
    tmp_path: Path,
) -> None:
    runtime = FullStackRuntime(tmp_path)
    accepted = False
    try:
        runtime.start()
        initialized = _run_backup(runtime, initialize=True)
        assert initialized.returncode == 0, "Backup monitor initialization failed"
        first = _run_backup(runtime)
        assert first.returncode == 0, "Scheduled backup did not succeed"
        _wait_for(
            lambda: (_metric(runtime, "prl_backup_job_last_success") or 0) == 1,
            "scheduled backup success metric",
        )
        assert (_metric(runtime, "prl_backup_last_verified_timestamp_seconds") or 0) > 0

        runtime.backup_env_file.write_text(
            "DATABASE_URL=mysql+asyncmy://prl_backup:invalid@mysql/prl\n"
            "BACKUP_MIN_FREE_BYTES=0\n",
            encoding="utf-8",
        )
        runtime.backup_env_file.chmod(0o600)
        failed = _run_backup(runtime)
        assert failed.returncode != 0
        _wait_for(
            lambda: _metric(runtime, "prl_backup_scheduled_job_failure_pending") == 1,
            "scheduled failure latch",
        )
        _wait_for(
            lambda: any(
                item["status"] == "firing"
                for item in runtime.alert_receiver_statuses("BackupJobFailed")
            ),
            "BackupJobFailed delivery to the local receiver",
        )

        runtime.backup_env_file.write_text(runtime.backup_env_text, encoding="utf-8")
        runtime.backup_env_file.chmod(0o600)
        recovered = _run_backup(runtime)
        assert recovered.returncode == 0, "Scheduled backup recovery did not succeed"
        _wait_for(
            lambda: _metric(runtime, "prl_backup_scheduled_job_failure_pending") == 0,
            "scheduled success clearing the failure latch",
        )
        _wait_for(
            lambda: any(
                item["status"] == "resolved"
                for item in runtime.alert_receiver_statuses("BackupJobFailed")
            ),
            "resolved BackupJobFailed delivery",
        )
        accepted = True
    finally:
        runtime.cleanup()
    assert accepted


def test_stopping_exporter_and_telemetry_does_not_break_backup_or_recovery(
    tmp_path: Path,
) -> None:
    runtime = FullStackRuntime(tmp_path)
    try:
        runtime.start()
        initialized = _run_backup(runtime, initialize=True)
        assert initialized.returncode == 0, "Backup monitor initialization failed"
        backup = _run_backup(runtime)
        assert backup.returncode == 0, "Scheduled backup did not succeed"
        reports = [json.loads(line) for line in backup.stdout.splitlines() if line.strip()]
        backup_id = next(
            (
                report.get("backup_id")
                for report in reports
                if report.get("success") is True and isinstance(report.get("backup_id"), str)
            ),
            None,
        )
        assert isinstance(backup_id, str)

        stopped = runtime.compose(
            "stop",
            "backup_metrics_exporter",
            "collector",
            "prometheus",
            "alertmanager",
            "local_alert_receiver",
        )
        assert stopped.returncode == 0, "Telemetry stack did not stop cleanly"
        while_telemetry_down = _run_backup(runtime)
        assert while_telemetry_down.returncode == 0, "Backup failed while telemetry was stopped"
        restored = _run_restore(runtime, backup_id)
        assert restored.returncode == 0, "Backup restore failed while telemetry was stopped"
    finally:
        runtime.cleanup()
