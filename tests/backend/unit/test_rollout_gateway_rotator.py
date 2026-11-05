import copy

import pytest
from tests.backend.unit.rollout_fixtures import release
from tests.backend.unit.test_rollout_runtime import config, runtime
from tools.deploy.release_manifest import canonical
from tools.deploy.rollout_state import RolloutError


def observability_runtime(tmp_path):
    host = runtime(tmp_path)
    host.overlay, host.observability = "https", True
    host.select(release(observability=True))
    return host


def test_rotator_is_recreated_after_its_namespace_owner(tmp_path, monkeypatch):
    host = observability_runtime(tmp_path)
    calls = []
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: calls.append(args))
    host.recreate_gateway()
    assert calls[0] == ("stop", "--timeout", "5", "gateway_log_rotate")
    assert calls[1][-1] == "gateway" and calls[2][-1] == "gateway_log_rotate"
    assert all("--force-recreate" in call and "--no-build" in call for call in calls[1:])
    assert not any("mysql" in call or "down" in call for call in calls)


def test_rotator_not_selected_for_the_local_profile(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    calls = []
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: calls.append(args))
    host.recreate_gateway()
    assert len(calls) == 1 and calls[0][-1] == "gateway"


@pytest.mark.parametrize("fault", [None, "pid_mode", "network_mode", "running", "reference"])
def test_rotator_must_run_in_both_namespaces_of_the_current_gateway(tmp_path, monkeypatch, fault):
    host = observability_runtime(tmp_path)
    assert host.manifest is not None
    gateway_id = "a" * 64
    owner = dict(id=gateway_id, running=True)
    helper = dict(
        id="b" * 64,
        running=True,
        pid_mode="container:" + gateway_id,
        network_mode="container:" + gateway_id,
        reference=host.manifest.vendor_images["gateway"],
        image="sha256:" + "c" * 64,
    )
    if fault is not None:
        helper[fault] = False if fault == "running" else "stale namespace or image"
    monkeypatch.setattr(host, "inspect", lambda name: owner if name == "gateway" else helper)
    monkeypatch.setattr(host, "command", lambda *args, **kwargs: b"sha256:" + b"c" * 64)
    if fault is None:
        host.verify_gateway_helpers()
    else:
        with pytest.raises(RolloutError, match="readiness-failed"):
            host.verify_gateway_helpers()


def test_rotator_plan_cannot_detach_from_its_reviewed_gateway_namespaces(tmp_path, monkeypatch):
    host = observability_runtime(tmp_path)
    assert host.manifest is not None
    plan = config(host.manifest)
    plan["services"]["backup_metrics_exporter"] = dict(
        image=host.manifest.images["backend"].reference
    )
    plan["services"]["gateway_log_rotate"] = dict(
        image=host.manifest.vendor_images["gateway"],
        pid="service:gateway",
        network_mode="service:gateway",
    )
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: canonical(plan))
    host.validate_plan()
    changed = copy.deepcopy(plan)
    changed["services"]["gateway_log_rotate"]["pid"] = "host"
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: canonical(changed))
    with pytest.raises(RolloutError, match="incompatible-configuration"):
        host.validate_plan()
