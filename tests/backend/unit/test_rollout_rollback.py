from tests.backend.unit.rollout_fixtures import controller, reference, release


def two_releases(tmp_path):
    host = controller(tmp_path)
    first = host.deploy(reference())
    host.runtime.previous_stable = first.stable
    host.registry.releases[reference(2)] = release(124, reference())
    second = host.deploy(reference(2))
    host.runtime.previous_stable = second.stable
    host.runtime.calls.clear()
    return host, second


def test_explicit_rollback_restores_reviewed_digests_without_migration(tmp_path):
    host, before = two_releases(tmp_path)
    result = host.rollback()
    assert result.operation is not None and result.operation.phase == "rolled_back"
    assert result.stable == before.previous_stable
    assert "migrate" not in host.runtime.calls and "grants" not in host.runtime.calls
    events = result.operation.events
    assert events[0].phase == "rollback_preflight"
    assert events[-1].elapsed_seconds - events[0].elapsed_seconds < 300
    assert [event.phase for event in events] == [
        "rollback_preflight",
        "rollback_rollout",
        "rollback_readiness",
        "rollback_smoke",
        "rolled_back",
    ]


def test_missing_previous_target_is_visible_without_changing_stable(tmp_path):
    host = controller(tmp_path)
    before = host.deploy(reference())
    result = host.rollback()
    assert result.refusal is not None and result.refusal.reason == "rollback-unavailable"
    assert result.stable == before.stable
    assert host.store.load() == result


def test_missing_rollback_images_preserve_stable_reference(tmp_path):
    host, before = two_releases(tmp_path)
    host.registry.missing = True
    result = host.rollback()
    assert result.operation is not None and result.operation.phase == "failed"
    assert result.operation.events[-1].reason == "missing-images"
    assert result.stable == before.stable and result.previous_stable == before.previous_stable
    assert "rollout" not in host.runtime.calls


def test_partial_schema_refuses_rollback_before_touching_services(tmp_path):
    host, before = two_releases(tmp_path)
    data = host.runtime.snapshot.model_dump()
    data["tables"].pop("audit_events")
    host.runtime.snapshot = type(host.runtime.snapshot)(**data)
    result = host.rollback()
    assert result.operation is not None and result.operation.events[-1].reason == "schema-partial"
    assert result.stable == before.stable
    assert "rollout" not in host.runtime.calls


def test_failed_rollback_keeps_failure_and_original_stable(tmp_path):
    host, before = two_releases(tmp_path)
    host.runtime.fail = "smoke"
    result = host.rollback()
    assert result.operation is not None and result.operation.phase == "failed"
    assert result.operation.events[-1].reason == "smoke-failed"
    assert result.stable == before.stable
    assert result.previous_stable == before.previous_stable
