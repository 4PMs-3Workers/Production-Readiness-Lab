import pytest
from tests.backend.unit.test_release_manifest import manifest_data
from tests.backend.unit.test_rollout_state import selected
from tools.deploy import release_images
from tools.deploy.release_images import Registry
from tools.deploy.release_manifest import Manifest, ReleaseError, canonical, parse
from tools.deploy.rollout_preflight import ReleaseConsumer
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


class FakeRegistry:
    def __init__(self):
        self.manifest = parse(canonical(manifest_data()), Manifest)
        self.calls = []

    def fetch(self, ref):
        self.calls.append("fetch")
        return self.manifest

    def verify_images(self, manifest, *, pull):
        assert pull is True
        self.calls.append("verify-and-pull")


def test_preflight_pins_exact_manifest_and_verifies_images_before_retention(tmp_path):
    registry, store = FakeRegistry(), StateStore(tmp_path)
    result = ReleaseConsumer(registry, store).prepare(selected().reference)
    assert registry.calls == ["fetch", "verify-and-pull"]
    assert store.manifest(result.selected) == result.manifest
    assert store.load() is None  # Retaining a candidate does not promote it.


def test_same_source_different_payload_cannot_replace_stable_target(tmp_path):
    registry = FakeRegistry()
    data = manifest_data()
    data["created_at"] = "2026-11-05T00:00:00Z"
    registry.manifest = parse(canonical(data), Manifest)
    with pytest.raises(RolloutError, match="invalid-manifest"):
        ReleaseConsumer(registry, StateStore(tmp_path)).prepare(
            selected().reference, expected=selected()
        )
    assert registry.calls == ["fetch"]


def test_missing_image_never_creates_a_stable_pointer(tmp_path, monkeypatch):
    registry = FakeRegistry()

    def unavailable(*args, **kwargs):
        raise ReleaseError("registry-unavailable")

    monkeypatch.setattr(registry, "verify_images", unavailable)
    store = StateStore(tmp_path)
    with pytest.raises(RolloutError, match="missing-images"):
        ReleaseConsumer(registry, store).prepare(selected().reference)
    assert store.load() is None
    assert not (tmp_path / "manifests").exists()


@pytest.mark.parametrize("elapsed", [0.0, 3.0])
def test_registry_tools_share_one_operation_deadline(monkeypatch, elapsed):
    now, calls = [10.0], []
    monkeypatch.setattr(release_images.time, "monotonic", lambda: now[0])

    def command(args, **kwargs):
        calls.append((args, kwargs))
        now[0] += elapsed
        return b"{}"

    monkeypatch.setattr(release_images, "command", command)
    registry = Registry(deadline=12.0)
    if elapsed:
        with pytest.raises(ReleaseError, match="deadline-exceeded"):
            registry.skopeo("inspect", "docker://example.invalid/image")
    else:
        assert registry.skopeo("inspect", "docker://example.invalid/image") == b"{}"
    assert calls[0][1]["timeout"] == 2.0
    now[0] = 13
    with pytest.raises(ReleaseError, match="deadline-exceeded"):
        registry.oras(["manifest", "fetch"], selected().reference)
    assert len(calls) == 1
