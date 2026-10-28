import json
import subprocess

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
