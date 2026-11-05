import os

import pytest
from tests.backend.unit.test_release_manifest import manifest_data
from tests.backend.unit.test_rollout_state import operation, selected
from tools.deploy.release_manifest import Manifest, canonical, parse
from tools.deploy.rollout_state import HostState, RolloutError
from tools.deploy.rollout_store import StateStore, replace_state


def state():
    return HostState(project="prl-demo", configuration_sha256="c" * 64)


def test_state_and_manifest_survive_new_store_instance(tmp_path):
    store = StateStore(tmp_path)
    manifest = parse(canonical(manifest_data()), Manifest)
    target = store.remember(selected().reference, manifest)
    store.save(state())
    assert StateStore(tmp_path).load() == state()
    assert StateStore(tmp_path).manifest(target) == manifest
    assert store.remember(target.reference, manifest) == target


def test_failed_atomic_replace_keeps_old_stable_and_cleans_temporary_file(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    store.save(state())
    original = store.path.read_bytes()

    def fail(*args):
        raise OSError("private filesystem details")

    monkeypatch.setattr(os, "replace", fail)
    op = operation()
    with pytest.raises(OSError):
        store.save(replace_state(state(), stable=op.target.model_dump(), operation=op.model_dump()))
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".release-*"))


def test_replacing_operation_retains_historical_authoritative_decision(tmp_path):
    store = StateStore(tmp_path)
    op = operation()
    first = replace_state(state(), stable=op.target.model_dump(), operation=op.model_dump())
    store.save(first)
    second = replace_state(first, operation=None)
    store.save(second)
    snapshots = list((tmp_path / "operations" / op.operation_id).glob("*.json"))
    assert len(snapshots) == 1
    assert parse(snapshots[0].read_bytes(), HostState) == first
    assert store.load() == second


def test_immutable_manifest_reference_cannot_be_rebound(tmp_path):
    store = StateStore(tmp_path)
    manifest = parse(canonical(manifest_data()), Manifest)
    store.remember(selected().reference, manifest)
    data = manifest_data()
    data["created_at"] = "2026-11-05T00:00:00Z"
    with pytest.raises(RolloutError, match="invalid-state"):
        store.remember(selected().reference, parse(canonical(data), Manifest))


@pytest.mark.parametrize("contents", [b"{", b"{}", b'{"format_version":1,"format_version":1}'])
def test_corrupt_journal_never_becomes_bootstrap(tmp_path, contents):
    store = StateStore(tmp_path)
    store.path.write_bytes(contents)
    with pytest.raises(RolloutError, match="invalid-state"):
        store.load()


def test_host_configuration_binding_cannot_be_replaced(tmp_path):
    store = StateStore(tmp_path)
    store.save(state())
    with pytest.raises(RolloutError, match="incompatible-configuration"):
        store.save(replace_state(state(), project="another-stack"))
    assert store.load() == state()


def test_missing_or_changed_cached_target_is_refused(tmp_path):
    store = StateStore(tmp_path)
    with pytest.raises(RolloutError, match="rollback-unavailable"):
        store.manifest(selected())
    manifest = parse(canonical(manifest_data()), Manifest)
    target = store.remember(selected().reference, manifest)
    bad = target.model_copy(update={"source_sha": "d" * 40})
    with pytest.raises(RolloutError, match="invalid-state"):
        store.manifest(bad)
