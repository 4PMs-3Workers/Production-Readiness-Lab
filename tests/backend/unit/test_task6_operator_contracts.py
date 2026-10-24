"""Task 6 operator and manifest contracts."""

import importlib.util
from datetime import UTC, datetime
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


def timestamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def v2_manifest(**updates):
    now = timestamp()
    value = {
        "manifest_version": 2,
        "source_sha": "a" * 40,
        "overlay": "https",
        "observability": True,
        "configuration_sha256": "b" * 64,
        "schema_revisions": ["20261019_1200_proposal_replay"],
        "images": {
            "collector": {"id": "sha256:" + "c" * 64, "digests": []},
            "local_alert_receiver": {"id": "sha256:" + "d" * 64, "digests": []},
        },
        "verification": {"command": "evidence", "status": "passed"},
        "observability_verification": {"command": "evidence", "status": "degraded"},
        "deployment_state": "active",
        "created_at": now,
        "updated_at": now,
    }
    value.update(updates)
    return value


def test_v2_manifest_records_independent_telemetry_result_and_services():
    evidence = module("evidence")
    manifest = evidence.validate(v2_manifest())
    assert manifest["verification"] == {"command": "evidence", "status": "passed"}
    assert manifest["observability_verification"] == {
        "command": "evidence",
        "status": "degraded",
    }
    assert set(manifest["images"]) == {"collector", "local_alert_receiver"}


def test_v2_manifest_keeps_disabled_telemetry_explicit_and_strict():
    evidence = module("evidence")
    value = v2_manifest(
        overlay="local",
        observability=False,
        observability_verification=None,
        images={"api": {"id": "sha256:" + "c" * 64, "digests": []}},
    )
    assert evidence.validate(value)["observability"] is False
    with pytest.raises(ValueError):
        evidence.validate(dict(value, unexpected="private-value"))
    with pytest.raises(ValueError):
        evidence.validate(
            dict(value, observability_verification={"command": "up", "status": "passed"})
        )


def test_v1_manifest_remains_readable_with_its_exact_historical_shape():
    evidence = module("evidence")
    value = evidence.create("a" * 40, "https", "up", "passed", {})
    assert evidence.validate(value)["manifest_version"] == 1
    with pytest.raises(ValueError):
        evidence.validate(dict(value, observability=False))


@pytest.fixture
def observability_environment(monkeypatch, tmp_path):
    demo = module("demo")
    for name in demo.ENV_FILES:
        path = tmp_path / name
        path.write_text("")
        monkeypatch.setenv(name, str(path))
    for name in (
        "PRL_TLS_CERT_FILE",
        "PRL_TLS_KEY_FILE",
        "PRL_OPS_TLS_CERT_FILE",
        "PRL_OPS_TLS_KEY_FILE",
    ):
        path = tmp_path / name
        path.write_text("certificate material")
        monkeypatch.setenv(name, str(path))
    for name in ("PRL_GRAFANA_ADMIN_USER_FILE", "PRL_GRAFANA_ADMIN_PASSWORD_FILE"):
        path = tmp_path / name
        path.write_text("private-credential-value")
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("PRL_HTTPS_ORIGIN", "https://app.example.invalid:8443")
    monkeypatch.setenv("PRL_OPS_ORIGIN", "https://ops.example.invalid:8443")
    monkeypatch.setenv("PRL_OPS_CIDRS", "203.0.113.10/32")
    monkeypatch.setenv("PRL_EXPECTED_WORKER_SLOTS", "1")
    for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"):
        monkeypatch.delenv(name, raising=False)
    return demo, tmp_path


def test_demo_selects_base_https_then_observability_and_checks_required_inputs(
    observability_environment,
):
    demo, _ = observability_environment
    instance = demo.Demo("https", "a" * 40, observability=True)
    selected = [
        instance.base[index + 1] for index, value in enumerate(instance.base[:-1]) if value == "-f"
    ]
    assert selected == [
        str(demo.ROOT / "tools/deploy/compose.yaml"),
        str(demo.ROOT / "tools/deploy/compose.https.yaml"),
        str(demo.ROOT / "tools/deploy/compose.observability.yaml"),
    ]


@pytest.mark.parametrize(
    "name,value",
    [
        ("PRL_OPS_CIDRS", ""),
        ("PRL_OPS_ORIGIN", "https://ops.example.invalid;include=/tmp/evil"),
        ("PRL_OPS_ORIGIN", "https://app.example.invalid:8443"),
        ("PRL_GRAFANA_ADMIN_PASSWORD_FILE", ""),
        ("PRL_OPS_TLS_KEY_FILE", ""),
    ],
)
def test_invalid_ops_inputs_fail_without_echoing_values(
    observability_environment, monkeypatch, name, value
):
    demo, _ = observability_environment
    monkeypatch.setenv(name, value)
    with pytest.raises(demo.OperationFailed) as caught:
        demo.Demo("https", "a" * 40, observability=True)
    if value:
        assert value not in str(caught.value)
    assert "private-credential-value" not in str(caught.value)


def test_observability_requires_https_before_any_compose_call(
    observability_environment, monkeypatch
):
    demo, _ = observability_environment
    calls = []
    monkeypatch.setattr(demo, "run", lambda args, timeout=180: calls.append(args) or "a" * 40)
    assert demo.main(["config", "--overlay", "local", "--observability"]) == 2
    assert not any(args[:2] == ["docker", "compose"] for args in calls)


def test_nonsecret_configuration_identity_changes_for_cidr_but_not_secret_contents(
    observability_environment, monkeypatch
):
    demo, _ = observability_environment
    before = demo.Demo("https", "a" * 40, observability=True)
    credential = Path(__import__("os").environ["PRL_GRAFANA_ADMIN_PASSWORD_FILE"])
    credential.write_text("different-private-credential")
    same_public_config = demo.Demo("https", "a" * 40, observability=True)
    assert before.configuration_sha256 == same_public_config.configuration_sha256
    monkeypatch.setenv("PRL_OPS_CIDRS", "203.0.113.11/32")
    changed_acl = demo.Demo("https", "a" * 40, observability=True)
    assert changed_acl.configuration_sha256 != same_public_config.configuration_sha256
    assert "different-private-credential" not in str(changed_acl.configuration_sha256)
