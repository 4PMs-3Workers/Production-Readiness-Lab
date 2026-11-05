import copy

import pytest
from tests.backend.unit.test_rollout_runtime import manifest, runtime
from tools.deploy.release_manifest import canonical
from tools.deploy.rollout_state import RolloutError


def record(host, service, image, reference) -> dict:
    return dict(
        id="a" * 64,
        image=image,
        reference=reference,
        running=True,
        health="healthy",
        labels={
            "com.docker.compose.project": host.project,
            "com.docker.compose.service": service,
            "org.opencontainers.image.revision": "a" * 40,
        },
        mounts=[
            dict(Type="volume", Name=f"{host.project}_mysql_data", Destination="/var/lib/mysql")
        ],
    )


@pytest.mark.parametrize(
    "field,value",
    [("image", "sha256:" + "f" * 64), ("reference", "unsafe:latest"), ("running", False)],
)
def test_running_services_must_match_exact_selected_images(tmp_path, monkeypatch, field, value):
    host, release = runtime(tmp_path), manifest()
    host.select(release)
    records = {}
    for service in ("api", "worker", "frontend"):
        image = release.images["frontend" if service == "frontend" else "backend"]
        records[service] = record(host, service, image.image_id, image.reference)
    monkeypatch.setattr(host, "inspect", lambda service: records[service])
    host.verify_images()
    records["worker"][field] = value
    monkeypatch.setattr(
        host,
        "command",
        lambda *args, **kwargs: canonical(
            {
                "id": "sha256:" + "f" * 64,
                "descriptor": None,
            }
        ),
    )
    with pytest.raises(RolloutError, match="image-mismatch"):
        host.verify_images()


def test_volume_identity_and_container_owner_are_preserved(tmp_path, monkeypatch):
    host, release = runtime(tmp_path), manifest()
    host.select(release)
    db = record(host, "mysql", "sha256:" + "d" * 64, release.vendor_images["mysql"])
    monkeypatch.setattr(host, "inspect", lambda service: db)

    def command(args, **kwargs):
        return (
            b"sha256:" + b"d" * 64
            if args[1] == "image"
            else canonical({"com.docker.compose.project": host.project})
        )

    monkeypatch.setattr(host, "command", command)
    before = host.database_identity()
    host.require_database(before)
    db["mounts"][0]["Name"] = "another-database-volume"
    with pytest.raises(RolloutError, match="volume-changed"):
        host.require_database(before)


def test_forged_compose_ownership_is_rejected_before_inspection_is_used(tmp_path, monkeypatch):
    host = runtime(tmp_path)
    host.select(manifest())
    db = record(host, "mysql", "sha256:" + "d" * 64, "image")
    db["labels"]["com.docker.compose.project"] = "another-project"
    monkeypatch.setattr(host, "compose", lambda *args, **kwargs: b"a" * 64)
    monkeypatch.setattr(host, "command", lambda *args, **kwargs: canonical(db))
    with pytest.raises(RolloutError, match="image-mismatch"):
        host.inspect("mysql")


def test_replacing_database_container_is_not_silently_accepted(tmp_path, monkeypatch):
    from tools.deploy.rollout_runtime import DatabaseIdentity

    host = runtime(tmp_path)
    old = DatabaseIdentity("a" * 64, "isolated_mysql_data")
    monkeypatch.setattr(host, "database_identity", lambda: copy.copy(old))
    host.require_database(old)
    monkeypatch.setattr(host, "database_identity", lambda: DatabaseIdentity("b" * 64, old.volume))
    with pytest.raises(RolloutError, match="volume-changed"):
        host.require_database(old)
