"""Docker-backed operator contracts run in the container acceptance workflow."""

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from tests.containers.test_compose_acceptance import ComposeAcceptance

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


def test_real_cli_migrate_and_seed_use_profiles_and_are_idempotent(
    clean_source_checkout, tmp_path, monkeypatch
):
    stack = ComposeAcceptance(tmp_path)
    checkout = clean_source_checkout
    deploy = checkout / "tools/deploy"
    manifest_path = checkout / "build/deploy"

    try:
        for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
            monkeypatch.delenv(name, raising=False)
        for name in (
            "PRL_DB_ENV_FILE",
            "PRL_RUNTIME_ENV_FILE",
            "PRL_MIGRATION_ENV_FILE",
            "PRL_SEED_ENV_FILE",
        ):
            monkeypatch.setenv(name, stack.env[name])
        monkeypatch.setenv("PRL_IMAGE_TAG", stack.image_tag)
        monkeypatch.setenv("PRL_HTTP_PORT", str(stack.port))
        monkeypatch.setenv("DOCKER_DEFAULT_PLATFORM", "linux/amd64")
        monkeypatch.setattr(sys, "path", sys.path.copy())

        evidence_spec = importlib.util.spec_from_file_location("evidence", deploy / "evidence.py")
        assert evidence_spec is not None and evidence_spec.loader is not None
        evidence = importlib.util.module_from_spec(evidence_spec)
        monkeypatch.setitem(sys.modules, "evidence", evidence)
        evidence_spec.loader.exec_module(evidence)

        cli_spec = importlib.util.spec_from_file_location("prl_demo_cli", deploy / "demo.py")
        assert cli_spec is not None and cli_spec.loader is not None
        cli = importlib.util.module_from_spec(cli_spec)
        monkeypatch.setitem(sys.modules, "prl_demo_cli", cli)
        cli_spec.loader.exec_module(cli)

        original_demo = cli.Demo

        class IsolatedProjectDemo(original_demo):
            def __init__(self, overlay, source_sha):
                super().__init__(overlay, source_sha)
                assert self.base[3] == "prl-demo"
                self.base[3] = stack.project

        monkeypatch.setattr(cli, "Demo", IsolatedProjectDemo)
        compose_steps = []
        original_run = cli.run

        def recording_run(args, timeout=180):
            if args[:2] == ["docker", "compose"]:
                compose_steps.append(args[args.index("-f") + 4 :])
            return original_run(args, timeout)

        monkeypatch.setattr(cli, "run", recording_run)

        assert cli.main(["build", "--overlay", "local"]) == 0
        assert cli.main(["migrate", "--overlay", "local"]) == 0
        assert cli.main(["seed", "--overlay", "local"]) == 0

        def user_counts():
            output = stack.sql("SELECT role, COUNT(*) FROM users GROUP BY role ORDER BY role;")
            return {
                role: int(count)
                for role, count in (line.split("\t") for line in output.splitlines())
            }

        expected_counts = {"ADMIN": 2, "STAFF": 10, "STUDENT": 30}
        first_counts = user_counts()
        assert first_counts == expected_counts
        assert cli.main(["seed", "--overlay", "local"]) == 0
        assert user_counts() == first_counts

        assert [
            "--profile",
            "operations",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ] in compose_steps
        assert [
            "--profile",
            "seed",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ] in compose_steps
        assert ["run", "--rm", "migrate"] in compose_steps
        assert ["exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh"] in compose_steps
        assert ["run", "--rm", "schema"] in compose_steps
        assert ["run", "--rm", "seed"] in compose_steps
        assert all("--profile" not in step or step[2] == "config" for step in compose_steps)

        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        manifest = json.loads((manifest_path / sha / "manifest.json").read_text())
        assert manifest["source_sha"] == sha
        assert manifest["verification"] == {"command": "seed", "status": "passed"}
        for reference in (
            f"prl-backend:{stack.image_tag}",
            f"prl-frontend:{stack.image_tag}",
        ):
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
        stack.cleanup()
