import json
import subprocess
import sys

from tests.backend.unit.rollout_fixtures import controller as fixture_controller
from tests.backend.unit.rollout_fixtures import reference
from tools.deploy import rollout
from tools.deploy.rollout_store import StateStore


def test_status_needs_no_credentials_and_does_not_claim_fresh_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rollout, "ROOT", tmp_path)
    assert rollout.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "empty"


def test_failed_deployment_even_with_successful_recovery_is_not_cli_deploy_success(
    tmp_path, monkeypatch, capsys
):
    from tests.backend.unit.rollout_fixtures import release

    host = fixture_controller(tmp_path)
    first = host.deploy(reference())
    host.runtime.previous_stable = first.stable
    host.registry.releases[reference(2)] = release(124, reference())
    host.runtime.fail, host.runtime.fail_release = "smoke", "release-124-1"
    monkeypatch.setattr(rollout, "controller", lambda *args: host.control)
    assert rollout.main(["deploy", reference(2), "--overlay", "local"]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["outcome"] == "rolled_back"
    assert value["state"]["stable"]["reference"] == reference()


def test_cli_rejects_unknown_input_without_echoing_it():
    completed = subprocess.run(
        [sys.executable, "-m", "tools.deploy.rollout", "deploy", "sensitive-password"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 1
    assert "sensitive-password" not in completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr


def test_legacy_lifecycle_cannot_bypass_managed_release_decisions(tmp_path, monkeypatch):
    from tests.backend.unit.test_demo_operator import module
    from tools.deploy.rollout_state import HostState

    demo = module("demo")
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    StateStore(tmp_path / "build/deploy").save(
        HostState(project="prl-demo", configuration_sha256="c" * 64)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy mutation reached the stack")

    monkeypatch.setattr(demo, "run", forbidden)
    assert demo.main(["up", "--overlay", "local"]) == 1
