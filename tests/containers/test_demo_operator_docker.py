"""Docker-backed operator contracts run in the container acceptance workflow."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "tools/deploy"


def clean_environment():
    env = dict(os.environ)
    for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
        env.pop(name, None)
    return env


@pytest.mark.parametrize("overlay", ["local", "https"])
def test_real_docker_configuration_never_resolves_external_env(tmp_path, overlay):
    env = clean_environment()
    tag = "task1-" + uuid.uuid4().hex
    env["PRL_IMAGE_TAG"] = tag
    for name in (
        "PRL_DB_ENV_FILE",
        "PRL_RUNTIME_ENV_FILE",
        "PRL_MIGRATION_ENV_FILE",
        "PRL_SEED_ENV_FILE",
    ):
        path = tmp_path / name
        path.write_text("PRIVATE_VALUE=do-not-print-this-value\n")
        env[name] = str(path)
    for name in ("PRL_TLS_CERT_FILE", "PRL_TLS_KEY_FILE"):
        path = tmp_path / name
        path.write_text("placeholder for config validation only")
        env[name] = str(path)
    env["PRL_HTTPS_ORIGIN"] = "https://localhost:8443"
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "demo.py"), "config", "--overlay", overlay],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "do-not-print-this-value" not in result.stdout + result.stderr


@pytest.fixture
def clean_source_checkout(tmp_path):
    checkout = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(ROOT), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=checkout, text=True
    )
    assert status == ""
    return checkout


def test_real_docker_build_binds_both_images_to_clean_head(clean_source_checkout, tmp_path):
    deploy = clean_source_checkout / "tools/deploy"
    env = clean_environment()
    tag = "task1-" + uuid.uuid4().hex
    env["PRL_IMAGE_TAG"] = tag
    references = [f"prl-backend:{tag}", f"prl-frontend:{tag}"]
    try:
        for name in (
            "PRL_DB_ENV_FILE",
            "PRL_RUNTIME_ENV_FILE",
            "PRL_MIGRATION_ENV_FILE",
            "PRL_SEED_ENV_FILE",
        ):
            path = tmp_path / name
            path.write_text("")
            env[name] = str(path)
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=clean_source_checkout, text=True
        ).strip()
        result = subprocess.run(
            [sys.executable, str(deploy / "demo.py"), "build", "--overlay", "local"],
            env=env,
            cwd=clean_source_checkout,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        assert result.returncode == 0, result.stderr
        for reference in references:
            revision = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                    reference,
                ],
                text=True,
            ).strip()
            assert revision == sha
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", *references],
            capture_output=True,
            text=True,
            timeout=30,
        )
