from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import tools.infra.check_telemetry as check_telemetry

ROOT = Path(__file__).resolve().parents[3]


def test_task6_validation_entrypoint_has_a_python_driver_and_help_contract():
    shell = ROOT / "tools/infra/check.sh"
    driver = ROOT / "tools/infra/check_telemetry.py"
    assert shell.is_file()
    assert driver.is_file()
    assert "check_telemetry.py" in shell.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(driver), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert "pinned" in result.stdout.lower()


def test_pinned_validator_process_arguments_are_flat_strings(monkeypatch):
    lock = json.loads((ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig"))
    commands = []
    monkeypatch.setattr(
        check_telemetry,
        "_record",
        lambda _run_id, _label, command, **_kwargs: commands.append(command),
    )
    check_telemetry._run_pinned_validators("contract", lock["images"])
    assert len(commands) == 7
    assert all(isinstance(argument, str) for command in commands for argument in command)
    assert any(
        command[1:4] == ["run", "--pull=never", "--rm"]
        and command[-3:] == ["test", "rules", "/etc/prometheus/rules/prometheus-rule-tests.yml"]
        for command in commands
    )


def test_container_acceptance_runs_locked_prometheus_rule_fixture(monkeypatch):
    telemetry_stack = importlib.import_module("tests.containers.test_telemetry_stack")
    calls: list[tuple[list[str], float]] = []

    def capture_invocation(args: list[str], *, timeout: float = 30):
        calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(telemetry_stack, "invoke", capture_invocation)
    runner = getattr(telemetry_stack, "_run_prometheus_rule_tests", None)
    assert callable(runner), "the required container suite must run the checked-in rule fixture"
    runner()

    assert len(calls) == 1
    command, timeout = calls[0]
    lock = json.loads((ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig"))
    assert timeout == 120
    assert command[:4] == ["docker", "run", "--pull=missing", "--rm"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--workdir") + 1] == "/etc/prometheus/rules"
    assert command[command.index("--entrypoint") + 2] == lock["images"]["prometheus"]
    assert command[-3:] == [
        "test",
        "rules",
        "/etc/prometheus/rules/prometheus-rule-tests.yml",
    ]
    assert (
        "type=bind,source="
        + str((ROOT / "tools/infra/prometheus-rules.yml").resolve())
        + ",target=/etc/prometheus/rules/prometheus-rules.yml,readonly"
    ) in command
    assert (
        "type=bind,source="
        + str((ROOT / "tools/infra/prometheus-rule-tests.yml").resolve())
        + ",target=/etc/prometheus/rules/prometheus-rule-tests.yml,readonly"
    ) in command


def test_temporary_compose_project_name_is_normalized_for_docker():
    project = check_telemetry._compose_project_name("20261024T132742Z-deadbeef")
    assert project == "prl-task6-check-20261024t132742z-deadbeef"


def test_compose_inventory_resolves_collector_and_gateway_base_locks():
    lock = json.loads((ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig"))
    services: dict[str, object] = {
        name: {"image": reference}
        for name, reference in lock["images"].items()
        if name not in {"collector", "gateway"}
    }
    services["gateway"] = {"image": lock["images"]["gateway"]}
    services["gateway_log_rotate"] = {"image": lock["images"]["gateway"]}
    services["collector"] = {
        "image": "prl-collector:check-only",
        "build": {
            "dockerfile": "tools/infra/collector.Dockerfile",
            "args": {"PRL_SOURCE_SHA": "a" * 40},
        },
    }
    services["api"] = {"environment": {"SERVICE_RELEASE": "a" * 40}}
    check_telemetry._validate_locked_images(services, lock["images"])
