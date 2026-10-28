import json
import os
import subprocess
import sys


def invoke(*args, env=None):
    environment = dict(os.environ)
    for name in ("DATABASE_URL", "RESTORE_DATABASE_URL", "BACKUP_STORE", "BACKUP_SOURCE_SHA"):
        environment.pop(name, None)
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "app.backup", *args],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_recovery_help_requires_no_database_credentials_or_resources():
    result = invoke("--help")
    assert result.returncode == 0 and "scheduled" in result.stdout and "restore" in result.stdout


def test_recovery_cli_never_echoes_bad_arguments_or_credentials(tmp_path):
    for args, env in [
        (("private-invalid-argument",), {}),
        (
            ("backup",),
            {
                "DATABASE_URL": "bad://private-user:private-password@host/db",
                "BACKUP_STORE": str(tmp_path),
            },
        ),
        (("restore",), {}),
    ]:
        result = invoke(*args, env=env)
        assert result.returncode == 2 and result.stderr == ""
        assert "private" not in result.stdout and "Traceback" not in result.stdout
        assert json.loads(result.stdout)["reason"] == "CONFIGURATION_INVALID"


def test_status_works_without_a_database_or_telemetry_connection(tmp_path):
    result = invoke(
        "status",
        env={
            "DATABASE_URL": "mysql+asyncmy://readonly:private@127.0.0.1:1/prl",
            "BACKUP_STORE": str(tmp_path),
        },
    )
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert value["last_successful_backup"] is None and value["backup_age_seconds"] is None
    assert (
        value["artifact_integrity_verified"] is False
        and value["application_rehearsal_verified"] is False
    )
