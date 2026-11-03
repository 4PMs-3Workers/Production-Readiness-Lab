import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from tests.containers.test_compose_acceptance import ComposeAcceptance
from tools.backup import run


def test_timeout_removes_only_labeled_owned_job(monkeypatch, capsys):
    removed = []
    monkeypatch.setattr(run, "configuration", lambda env: (["docker", "compose"], 30))

    def invoke(args, env, timeout=10):
        if "scheduled" in args:
            raise subprocess.TimeoutExpired(args, timeout)
        removed.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def inspected(args, env):
        name = args[-1]
        return {"Name": "/" + name, "Config": {"Labels": {"prl.backup.job": name[15:]}}}

    monkeypatch.setattr(run, "invoke", invoke)
    monkeypatch.setattr(run, "inspected", inspected)
    assert run.main() == 1
    assert len(removed) == 1 and removed[0][:4] == ["docker", "container", "rm", "--force"]
    assert json.loads(capsys.readouterr().out)["reason"] == "HOST_JOB_FAILED"


def test_cleanup_refuses_unowned_container(monkeypatch):
    monkeypatch.setattr(run, "inspected", lambda *args: {"Name": "/job", "Config": {}})
    monkeypatch.setattr(run, "invoke", lambda *args: (_ for _ in ()).throw(AssertionError()))
    run.remove_owned("job", "our-token", {})


def test_raw_docker_errors_never_reach_output(monkeypatch, capsys):
    monkeypatch.setattr(run, "configuration", lambda env: (["docker", "compose"], 30))
    monkeypatch.setattr(run, "remove_owned", lambda *args: None)
    monkeypatch.setattr(
        run, "invoke", lambda *args: subprocess.CompletedProcess([], 1, "", "secret-url")
    )
    assert run.main() == 1
    assert "secret" not in capsys.readouterr().out


def test_monitor_initialization_identifies_host_failure_stage_without_raw_output(
    monkeypatch, capsys
):
    monkeypatch.setattr(run, "configuration", lambda env: (["docker", "compose"], 30))
    monkeypatch.setattr(run, "remove_owned", lambda *args: None)
    monkeypatch.setattr(
        run,
        "invoke",
        lambda args, env, timeout=10: subprocess.CompletedProcess(args, 1, "", "secret"),
    )

    assert run.main(initialize_monitor_state=True) == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "operation": "monitor-init",
        "success": False,
        "reason": "HOST_JOB_FAILED",
        "stage": "container_job",
    }


def test_compose_acceptance_writes_host_job_compatible_secret_files(tmp_path, monkeypatch):
    stack = cast(
        ComposeAcceptance,
        SimpleNamespace(
            scratch=tmp_path,
            origin="http://localhost:8080",
            db_passwords={role: "private" for role in ("root", "migration", "runtime", "seed")},
            seed_passwords={role: "private" for role in ("ADMIN", "STAFF", "STUDENT")},
        ),
    )
    original_chmod = Path.chmod
    protected = []

    def record_chmod(path, mode):
        protected.append((path, mode))
        original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    previous_umask = os.umask(0o022)
    try:
        env_files = ComposeAcceptance._write_env_files(stack)
    finally:
        os.umask(previous_umask)

    assert len(env_files) == 4
    assert {(str(path.resolve()), mode) for path, mode in protected} == {
        (file_name, 0o600) for file_name in env_files.values()
    }
    for file_name in env_files.values():
        if os.name != "nt":
            assert os.stat(file_name).st_mode & 0o077 == 0


def test_runner_initialization_uses_validated_backup_image(monkeypatch, capsys):
    configured = []
    invoked = []
    monkeypatch.setattr(
        run,
        "configuration",
        lambda env: (configured.append(env) or (["docker", "compose", "--validated-image"], 30)),
    )

    def invoke(args, env, timeout=10):
        invoked.append(args)
        report = {
            "operation": "monitor-init",
            "success": True,
            "duration_seconds": 0.1,
            "state_schema_version": 1,
            "initialized": True,
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(report) + "\n", "")

    monkeypatch.setattr(run, "invoke", invoke)
    monkeypatch.setattr(run, "remove_owned", lambda *args: None)

    assert run.main(initialize_monitor_state=True) == 0
    assert configured and invoked[0][-1] == "monitor-init"
    assert json.loads(capsys.readouterr().out)["operation"] == "monitor-init"


def test_monitor_initialization_reports_only_allowlisted_child_failure_reason(monkeypatch, capsys):
    monkeypatch.setattr(run, "configuration", lambda env: (["docker", "compose"], 30))
    monkeypatch.setattr(run, "remove_owned", lambda *args: None)
    monkeypatch.setattr(
        run,
        "invoke",
        lambda args, env, timeout=10: subprocess.CompletedProcess(
            args,
            1,
            json.dumps(
                {
                    "operation": "monitor-init",
                    "success": False,
                    "reason": "STORAGE_UNAVAILABLE_OR_CAPACITY_EXCEEDED",
                }
            )
            + "\n",
            "private mount detail",
        ),
    )

    assert run.main(initialize_monitor_state=True) == 1

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "operation": "monitor-init",
        "success": False,
        "reason": "STORAGE_UNAVAILABLE_OR_CAPACITY_EXCEEDED",
    }
    assert "private" not in output
