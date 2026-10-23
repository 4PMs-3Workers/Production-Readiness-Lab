"""Deployment CLI must fail closed without exposing invalid configuration."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("command", ["schema", "migrate"])
def test_deployment_commands_reject_bad_database_url_without_echoing_secrets(command):
    env = dict(os.environ, DATABASE_URL="invalid-driver://private-user:private-password@bad/db")
    env["PYTHONPATH"] = "src/backend"
    result = subprocess.run(
        [sys.executable, "-m", "app.deploy", command],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Deployment operation failed" in result.stderr
    assert "private-user" not in result.stderr
    assert "private-password" not in result.stderr
    assert "Traceback" not in result.stderr


def test_unknown_deployment_command_never_echoes_arguments():
    result = subprocess.run(
        [sys.executable, "-m", "app.deploy", "secret-as-invalid-argument"],
        env=dict(os.environ, PYTHONPATH="src/backend"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "secret-as-invalid-argument" not in result.stderr
    assert "Usage:" in result.stderr
