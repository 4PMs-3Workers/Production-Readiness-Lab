import pytest
from tests.backend.unit.rollout_fixtures import controller, reference, release
from tools.deploy.release_manifest import canonical, parse
from tools.deploy.rollout_state import HostState, RolloutError
from tools.deploy.rollout_store import replace_state


def test_uncommitted_retirement_then_refusal_cannot_permanently_block_valid_retry(
    tmp_path, monkeypatch
):
    host = controller(tmp_path)
    before = host.deploy(reference())
    host.runtime.previous_stable = before.stable
    host.registry.releases[reference(2)] = release(124, reference())
    original = host.store._write
    failures = [0]

    def fail_authoritative_begin(path, value):
        if (
            path == host.store.path
            and value.operation is not None
            and value.operation.target.reference == reference(2)
            and failures[0] == 0
        ):
            failures[0] += 1
            raise OSError("private disk error after writing archive")
        original(path, value)

    monkeypatch.setattr(host.store, "_write", fail_authoritative_begin)
    with pytest.raises(OSError):
        host.deploy(reference(2))
    assert host.store.load() == before
    refused = host.deploy(reference(99))
    assert refused.refusal is not None and refused.stable == before.stable
    completed = host.deploy(reference(2))
    assert completed.operation is not None and completed.operation.phase == "succeeded"
    assert completed.previous_stable == before.stable
    snapshots = [
        parse(path.read_bytes(), HostState)
        for path in (host.store.directory / "operations").rglob("*.json")
    ]
    assert before in snapshots and refused in snapshots


def test_legacy_flat_archive_is_preserved_without_poisoning_updated_authoritative_state(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    assert before.operation is not None
    legacy = host.store.directory / "operations" / f"{before.operation.operation_id}.json"
    legacy.parent.mkdir()
    legacy.write_bytes(canonical(before))
    host.deploy(reference(99))
    host.runtime.previous_stable = before.stable
    host.registry.releases[reference(2)] = release(124, reference())
    completed = host.deploy(reference(2))
    assert completed.operation is not None and completed.operation.phase == "succeeded"
    assert legacy.read_bytes() == canonical(before)


def test_nested_archive_directory_cannot_redirect_a_snapshot(tmp_path, monkeypatch):
    host = controller(tmp_path)
    before = host.deploy(reference())
    redirected = host.store.directory / "operations"
    original = type(redirected).is_symlink
    monkeypatch.setattr(
        type(redirected), "is_symlink", lambda path: path == redirected or original(path)
    )
    with pytest.raises(RolloutError, match="invalid-state"):
        host.store.save(replace_state(before, operation=None))
    assert host.store.load() == before
