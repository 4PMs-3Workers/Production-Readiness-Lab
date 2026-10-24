from __future__ import annotations

import json
import subprocess
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
        [str(ROOT / ".venv/Scripts/python.exe"), str(driver), "--help"],
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
    assert len(commands) == 6
    assert all(isinstance(argument, str) for command in commands for argument in command)


def test_temporary_compose_project_name_is_normalized_for_docker():
    project = check_telemetry._compose_project_name("20261024T132742Z-deadbeef")
    assert project == "prl-task6-check-20261024t132742z-deadbeef"


def test_compose_inventory_resolves_collector_and_gateway_base_locks():
    lock = json.loads((ROOT / "tools/infra/images.lock.json").read_text(encoding="utf-8-sig"))
    services = {
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
