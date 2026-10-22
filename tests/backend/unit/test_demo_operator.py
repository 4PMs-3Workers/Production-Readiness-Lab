"""Contract tests for the operator entry point and provenance schema."""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "tools/deploy"


def module(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_evidence_strict_and_atomic(tmp_path):
    evidence = module("evidence")
    path = tmp_path / "manifest.json"
    value = evidence.create("a" * 40, "local", "config", "passed", {})
    evidence.write(path, value)
    assert evidence.validate(json.loads(path.read_text())) == value
    for field, invalid in [
        ("source_sha", "secret"),
        ("overlay", "both"),
        ("verification", {"command": "up", "status": "magic"}),
    ]:
        bad = dict(value, **{field: invalid})
        with pytest.raises(ValueError):
            evidence.validate(bad)
    with pytest.raises(ValueError):
        evidence.validate(dict(value, DATABASE_URL="secret"))
    with pytest.raises(ValueError):
        evidence.validate(dict(value, images={"api": {"id": "secret"}}))


def test_overlay_required_and_no_argument_echo():
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "demo.py"), "config", "secret"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "secret" not in result.stdout + result.stderr


def test_worker_requires_heartbeat_progress_and_tolerates_db_clock_skew():
    demo = module("demo")
    from datetime import datetime, timedelta

    now = datetime.now(UTC)

    def event(kind, age=0):
        return json.dumps(
            {
                "event": kind,
                "phase": "running",
                "database_ok": True,
                "successful_scans": 2,
                "last_successful_scan": (now - timedelta(seconds=age)).isoformat(),
                "worker_id": "a" * 32,
            }
        )

    assert not demo.worker_ready(event("scan"))
    assert demo.worker_ready(event("heartbeat", 999))
    assert demo.worker_ready(event("heartbeat"))


@pytest.fixture
def operator(monkeypatch, tmp_path):
    demo = module("demo")
    for name in demo.ENV_FILES:
        path = tmp_path / name
        path.write_text("DO_NOT_PRINT=private-password\n$(touch should-not-exist)")
        monkeypatch.setenv(name, str(path))
    for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    calls = []

    def fake(args, timeout=180):
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "a" * 40
        if args[:2] == ["docker", "compose"]:
            index = args.index("-f")
            tail = args[index + 4 :]
            assert tail[:1] != ["config"] or tail in (
                ["config", "--quiet"],
                ["config", "--no-env-resolution", "--format", "json"],
            )
            if tail == ["config", "--no-env-resolution", "--format", "json"]:
                return json.dumps(
                    {
                        "services": {
                            service: {"image": "fake-image"}
                            for service in demo.evidence.SERVICES | demo.APPLICATION_SERVICES
                        }
                    }
                )
            if tail[:2] == ["ps", "-q"]:
                return "b" * 64
            if tail[:2] == ["images", "-q"]:
                return "sha256:" + "c" * 64
            if tail[:1] == ["logs"]:
                from datetime import datetime

                return json.dumps(
                    {
                        "event": "heartbeat",
                        "phase": "running",
                        "database_ok": True,
                        "worker_id": "a" * 32,
                        "successful_scans": len(calls),
                        "last_successful_scan": datetime.now(UTC).isoformat(),
                    }
                )
        if args[:2] == ["docker", "inspect"]:
            return "true" if "{{.State.Running}}" in args else "healthy"
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                {
                    "Id": "sha256:" + "c" * 64,
                    "RepoDigests": [],
                    "Labels": {demo.REVISION_LABEL: "a" * 40},
                }
            )
        return ""

    monkeypatch.setattr(demo, "run", fake)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(demo, "urlopen", lambda *args, **kwargs: Response())
    return demo, calls


def compose_calls(calls):
    return [call[call.index("-f") + 4 :] for call in calls if call[:2] == ["docker", "compose"]]


def test_explicit_migration_order(operator):
    demo, calls = operator
    assert demo.main(["migrate", "--overlay", "local"]) == 0
    steps = [step for step in compose_calls(calls) if "--no-env-resolution" not in step]
    assert steps[:6] == [
        ["config", "--quiet"],
        ["up", "-d", "mysql"],
        ["ps", "-q", "mysql"],
        ["run", "--rm", "migrate"],
        ["exec", "-T", "mysql", "bash", "/opt/prl/apply-grants.sh"],
        ["run", "--rm", "schema"],
    ]


def test_up_never_runs_privileged_jobs_and_writes_provenance(operator):
    demo, calls = operator
    assert demo.main(["up", "--overlay", "local"]) == 0
    steps = compose_calls(calls)
    assert ["run", "--rm", "schema"] in steps
    assert ["up", "-d", "api", "frontend", "gateway", "worker"] in steps
    assert not any("migrate" in step or "seed" in step or "exec" in step for step in steps)
    data = json.loads((demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").read_text())
    assert data["verification"] == {"command": "up", "status": "passed"}
    assert data["images"]["api"] == data["images"]["worker"]
    assert "private-password" not in json.dumps(data)


@pytest.mark.parametrize(
    "command", ["stop", "down", "config", "seed", "build", "status", "evidence"]
)
def test_commands_preserve_volumes_and_seed_is_explicit(operator, command):
    demo, calls = operator
    assert demo.main([command, "--overlay", "local"]) == 0
    steps = compose_calls(calls)
    assert not any("--volumes" in step or "-v" in step for step in steps)
    assert (["run", "--rm", "seed"] in steps) == (command == "seed")


@pytest.mark.parametrize("failure", ["migrate", "grants", "schema"])
def test_failure_stops_lifecycle_and_sanitizes(operator, monkeypatch, capsys, failure):
    demo, calls = operator
    original = demo.run

    def failed(args, timeout=180):
        if (
            (failure == "migrate" and args[-3:] == ["run", "--rm", "migrate"])
            or (failure == "grants" and args[-1:] == ["/opt/prl/apply-grants.sh"])
            or (failure == "schema" and args[-3:] == ["run", "--rm", "schema"])
        ):
            raise RuntimeError("DATABASE_URL=private-password")
        return original(args, timeout)

    monkeypatch.setattr(demo, "run", failed)
    assert demo.main(["migrate", "--overlay", "local"]) == 1
    output = capsys.readouterr()
    assert "private-password" not in output.out + output.err
    steps = compose_calls(calls)
    if failure == "migrate":
        assert not any("exec" in step or "schema" in step for step in steps)
    data = json.loads((demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").read_text())
    assert data["verification"]["status"] == "failed"


@pytest.mark.parametrize(
    "name",
    ["PRL_DB_ENV_FILE", "PRL_RUNTIME_ENV_FILE", "PRL_MIGRATION_ENV_FILE", "PRL_SEED_ENV_FILE"],
)
def test_all_env_paths_required_even_inactive(operator, monkeypatch, name):
    demo, calls = operator
    monkeypatch.delenv(name)
    assert demo.main(["config", "--overlay", "local"]) == 1
    assert not compose_calls(calls)


def test_os_lock_rejects_contention_and_releases_after_exit(tmp_path):
    evidence = module("evidence")
    path = tmp_path / "lock"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import evidence; "
        "ctx=evidence.lifecycle_lock(sys.argv[2]); ctx.__enter__()"
    )
    with evidence.lifecycle_lock(path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(DEPLOY), str(path)], capture_output=True, text=True
        )
        assert result.returncode != 0
    result = subprocess.run(
        [sys.executable, "-c", code, str(DEPLOY), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0


def test_subprocess_failure_discards_both_output_streams(capsys):
    demo = module("demo")
    with pytest.raises(demo.OperationFailed) as caught:
        demo.run(
            [
                sys.executable,
                "-c",
                "import sys; print('secret'); print('secret', file=sys.stderr); sys.exit(1)",
            ]
        )
    assert "secret" not in str(caught.value)
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err


@pytest.mark.parametrize("overlay", ["local", "https"])
@pytest.mark.skipif(
    os.environ.get("PRL_RUN_DOCKER_TESTS") != "1", reason="opt-in Docker config check"
)
def test_real_docker_configuration_never_resolves_external_env(tmp_path, overlay):
    env = dict(os.environ)
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


def test_status_is_read_only_and_never_starts_dependencies(operator):
    demo, calls = operator
    assert demo.main(["status", "--overlay", "local"]) == 0
    steps = compose_calls(calls)
    assert ["ps", "--all", "--format", "json"] in steps
    assert not any(step[0] in {"run", "up", "exec"} for step in steps)


def test_build_records_images_without_existing_containers(operator, monkeypatch):
    demo, calls = operator
    original = demo.run

    def no_containers(args, timeout=180):
        if args[-3:-1] == ["images", "-q"]:
            return ""
        return original(args, timeout)

    monkeypatch.setattr(demo, "run", no_containers)
    assert demo.main(["build", "--overlay", "local"]) == 0
    data = json.loads((demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").read_text())
    assert set(data["images"]) == demo.evidence.SERVICES


def test_status_does_not_echo_container_names_labels_or_ports(operator, monkeypatch, capsys):
    demo, calls = operator
    original = demo.run

    def status(args, timeout=180):
        if args[-4:] == ["ps", "--all", "--format", "json"]:
            return json.dumps(
                {
                    "Service": "api",
                    "State": "running",
                    "Health": "healthy",
                    "Name": "private-password",
                }
            )
        return original(args, timeout)

    monkeypatch.setattr(demo, "run", status)
    assert demo.main(["status", "--overlay", "local"]) == 0
    captured = capsys.readouterr()
    assert "api: running, health=healthy" in captured.out
    assert "private-password" not in captured.out + captured.err


def test_fresh_heartbeat_without_advancing_scans_cannot_pass_up(operator, monkeypatch):
    demo, calls = operator
    original = demo.run

    def frozen(args, timeout=180):
        output = original(args, timeout)
        if "logs" in args:
            record = json.loads(output)
            record["successful_scans"] = 1
            return json.dumps(record)
        return output

    def bounded(self, predicate):
        for _ in range(3):
            if predicate():
                return
        raise demo.OperationFailed()

    monkeypatch.setattr(demo, "run", frozen)
    monkeypatch.setattr(demo.Demo, "wait", bounded)
    assert demo.main(["up", "--overlay", "local"]) == 1


@pytest.mark.parametrize("command", ["up", "migrate", "seed", "evidence"])
def test_stale_images_fail_before_any_application_execution(operator, monkeypatch, capsys, command):
    demo, calls = operator
    original = demo.run

    def stale(args, timeout=180):
        result = original(args, timeout)
        if args[:3] == ["docker", "image", "inspect"]:
            image = json.loads(result)
            image["Labels"] = {"org.opencontainers.image.revision": "d" * 40}
            return json.dumps(image)
        return result

    monkeypatch.setattr(demo, "run", stale)
    assert demo.main([command, "--overlay", "local"]) == 1
    assert not any(step[0] in {"run", "up", "exec"} for step in compose_calls(calls))
    captured = capsys.readouterr()
    assert "private-password" not in captured.out + captured.err


@pytest.mark.parametrize("dirty", [" M Dockerfile.backend", "?? private-secret-file"])
def test_dirty_tree_is_never_attributed_to_head(operator, monkeypatch, capsys, dirty):
    demo, calls = operator
    original = demo.run

    def changed(args, timeout=180):
        if args[:2] == ["git", "status"]:
            calls.append(args)
            return dirty
        return original(args, timeout)

    monkeypatch.setattr(demo, "run", changed)
    assert demo.main(["build", "--overlay", "local"]) == 1
    assert not compose_calls(calls)
    assert not (demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").exists()
    captured = capsys.readouterr()
    assert "private-secret-file" not in captured.out + captured.err


def test_status_stale_snapshot_cannot_emit_passed_manifest(operator, monkeypatch):
    demo, calls = operator
    original = demo.run

    def stale(args, timeout=180):
        result = original(args, timeout)
        if args[:3] == ["docker", "image", "inspect"]:
            image = json.loads(result)
            image["Labels"] = {"org.opencontainers.image.revision": "d" * 40}
            return json.dumps(image)
        return result

    monkeypatch.setattr(demo, "run", stale)
    assert demo.main(["status", "--overlay", "local"]) == 1
    value = json.loads((demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").read_text())
    assert value["verification"]["status"] == "failed"
    assert value["images"] == {}


@pytest.mark.parametrize(
    "labels", [None, {}, {"org.opencontainers.image.revision": "private-password"}]
)
def test_missing_or_invalid_revision_labels_refuse_migration(operator, monkeypatch, labels):
    demo, calls = operator
    original = demo.run

    def unlabeled(args, timeout=180):
        result = original(args, timeout)
        if args[:3] == ["docker", "image", "inspect"]:
            image = json.loads(result)
            image["Labels"] = labels
            return json.dumps(image)
        return result

    monkeypatch.setattr(demo, "run", unlabeled)
    assert demo.main(["migrate", "--overlay", "local"]) == 1
    assert not any(step[0] in {"run", "up", "exec"} for step in compose_calls(calls))


def test_build_passes_exact_git_revision_to_both_images(operator):
    demo, calls = operator
    assert demo.main(["build", "--overlay", "local"]) == 0
    assert [
        "build",
        "--build-arg",
        "PRL_SOURCE_SHA=" + "a" * 40,
        "api",
        "frontend",
    ] in compose_calls(calls)


def test_changed_head_during_operation_cannot_emit_passed_manifest(operator, monkeypatch):
    demo, calls = operator
    original = demo.run

    def changed(args, timeout=180):
        result = original(args, timeout)
        if args == ["git", "rev-parse", "HEAD"] and any("build" in call for call in calls):
            return "d" * 40
        return result

    monkeypatch.setattr(demo, "run", changed)
    assert demo.main(["build", "--overlay", "local"]) == 1
    value = json.loads((demo.ROOT / "build/deploy" / ("a" * 40) / "manifest.json").read_text())
    assert value["verification"]["status"] == "failed"


@pytest.mark.skipif(
    os.environ.get("PRL_RUN_DOCKER_BUILD_TESTS") != "1",
    reason="opt-in image build provenance check",
)
def test_real_docker_build_binds_both_images_to_clean_head(tmp_path):
    env = dict(os.environ)
    for name in (
        "PRL_DB_ENV_FILE",
        "PRL_RUNTIME_ENV_FILE",
        "PRL_MIGRATION_ENV_FILE",
        "PRL_SEED_ENV_FILE",
    ):
        path = tmp_path / name
        path.write_text("")
        env[name] = str(path)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "demo.py"), "build", "--overlay", "local"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    for reference in ("prl-backend:local", "prl-frontend:local"):
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
