import json

import pytest
from tests.backend.unit.test_release_manifest import manifest_data
from tools.deploy.release_manifest import GRANTS_PATH, ROOT, Manifest, canonical, parse
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime
from tools.deploy.rollout_state import RolloutError


def runtime(tmp_path):
    return ReleaseRuntime(
        ["docker", "compose", "--project-name", "isolated"],
        {},
        "http://localhost:8080",
        "isolated",
        tmp_path,
    )


def manifest():
    return parse(canonical(manifest_data()), Manifest)


def config(value):
    services = {
        name: dict(image=value.images[key].reference)
        for name, key in value.services.items()
        if name != "backup"
    }
    services.update({name: dict(image=ref) for name, ref in value.vendor_images.items()})
    return dict(services=services)


def test_plan_pins_images_and_removes_builds_before_service_changes(tmp_path, monkeypatch):
    host, release = runtime(tmp_path), manifest()
    host.select(release)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return canonical(config(release))

    monkeypatch.setattr(host, "command", command)
    assert host.validate_plan()["services"]["api"]["image"] == release.images["backend"].reference
    assert len(calls) == 1 and "config" in calls[0]
    assert host.environment["SERVICE_RELEASE"] == release.provenance.source_sha
    assert host.overlay_path is not None
    assert b"build: !reset null" in host.overlay_path.read_bytes()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["services"]["api"].update(build={"context": "."}),
        lambda d: d["services"]["worker"].update(image="image:latest"),
        lambda d: d["services"].pop("frontend"),
    ],
)
def test_build_fallback_or_reference_drift_is_refused(tmp_path, monkeypatch, change):
    host, release = runtime(tmp_path), manifest()
    host.select(release)
    data = config(release)
    change(data)
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: json.dumps(data).encode())
    with pytest.raises(RolloutError, match="incompatible-configuration"):
        host.validate_plan()


def test_rollout_never_drops_volume_downgrades_or_rebuilds(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    host.select(manifest())
    calls = []
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: calls.append(args))
    host.migrate()
    host.rollout()
    assert not any(
        word in {"down", "--volumes", "downgrade", "build"} for call in calls for word in call
    )
    assert all("--no-build" in call for call in calls if call[0] == "up")
    assert any("--no-deps" in call and "migrate" in call for call in calls)
    assert not any("mysql" in call for call in calls)


def test_existing_mysql_receives_verified_grants_over_stdin(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    host.select(manifest())
    sql = (ROOT / GRANTS_PATH).read_bytes()
    host.root = tmp_path
    path = tmp_path / GRANTS_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(sql)
    calls = []
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: calls.append((args, kwargs)))
    host.apply_grants()
    [(args, options)] = calls
    assert args[:3] == ("exec", "-T", "mysql")
    assert options["input_data"] == sql
    assert "/opt/prl/apply-grants.sh" not in args
    path.write_bytes(sql + b"\n-- unexpected change\n")
    with pytest.raises(RolloutError, match="incompatible-configuration"):
        host.apply_grants()
    assert len(calls) == 1


def test_deadline_never_restarts_the_budget_between_stages():
    now = [10.0]
    budget = Deadline(300, clock=lambda: now[0])
    now[0] = 309
    assert budget.remaining(30) == 1
    now[0] = 310
    with pytest.raises(RolloutError, match="deadline-exceeded"):
        budget.remaining()
