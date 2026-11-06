import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from tests.backend.unit.offhost_fixtures import Store
from tests.backend.unit.test_offhost_contract import configuration
from tools.backup.offhost_contract import (
    Completion,
    RemoteConfig,
    RemoteError,
    canonical,
    payload_key,
    point_key,
)
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_retention import retain
from tools.backup.offhost_s3 import S3


def stamp(instant):
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def points(tmp_path, *, keep=1):
    config = RemoteConfig.model_validate(
        dict(configuration().model_dump(), keep_count=keep, max_age_days=1)
    )
    store, now, values = Store(), datetime.now(UTC), []
    for number in range(3):
        instant = now - timedelta(days=5 - number)
        blob = f"encrypted-fixture-{number}".encode()
        point = Completion.model_validate(
            dict(
                backup_id="prl-backup-20261001T000000Z-" + f"{number:032x}",
                source_id=config.source_id,
                configuration_sha256=config.identity,
                source_sha="a" * 40,
                backend_image_id="sha256:" + "b" * 64,
                backup_manifest_sha256="c" * 64,
                release_sha256="d" * 64,
                bundle_sha256="e" * 64,
                encrypted_sha256=hashlib.sha256(blob).hexdigest(),
                encrypted_bytes=len(blob),
                snapshot=dict(established_before=stamp(instant), established_after=stamp(instant)),
                backup_completed_at=stamp(instant),
                verified_at=stamp(instant),
            )
        )
        store.objects[point_key(config, point.backup_id)] = canonical(point)
        store.objects[payload_key(config, point)] = blob
        values.append(point)
    remote = S3(config, Deadline(10), client=store)
    return remote, values, now


def test_expired_last_verified_point_and_every_unrelated_object_are_preserved(tmp_path):
    remote, values, now = points(tmp_path)
    unrelated = {
        "other/environment/keep": b"unrelated",
        remote.config.scope + "points/unknown/complete.json": b"unowned",
        remote.config.scope + "points/" + values[0].backup_id + "/unrelated": b"keep",
    }
    remote.client.objects.update(unrelated)
    report = retain(remote, tmp_path / "work", now=now)
    assert report == dict(removed_count=2, preserved_count=1, ignored_entries=2)
    assert point_key(remote.config, values[-1].backup_id) in remote.client.objects
    assert payload_key(remote.config, values[-1]) in remote.client.objects
    assert all(remote.client.objects[key] == value for key, value in unrelated.items())


@pytest.mark.parametrize("fault", ["corrupt", "missing", "identity"])
def test_invalid_inventory_aborts_before_any_deletion(tmp_path, fault):
    remote, values, now = points(tmp_path)
    if fault == "corrupt":
        remote.client.objects[payload_key(remote.config, values[0])] = b"bad"
    elif fault == "missing":
        remote.client.objects.pop(payload_key(remote.config, values[0]))
    else:
        data = values[0].model_dump(mode="json")
        data["source_id"] = "b" * 64
        remote.client.objects[point_key(remote.config, values[0].backup_id)] = canonical(data)
    with pytest.raises(RemoteError):
        retain(remote, tmp_path / "work", now=now)
    assert not any(operation == "delete" for operation, _ in remote.client.calls)


def test_count_boundary_and_new_point_after_inventory_are_safe(tmp_path):
    remote, values, now = points(tmp_path, keep=3)
    # No age pressure: all three requested points remain, including the boundary.
    remote.config = RemoteConfig.model_validate(dict(remote.config.model_dump(), max_age_days=100))
    assert retain(remote, tmp_path / "work", now=now)["removed_count"] == 0
    remote.config = RemoteConfig.model_validate(dict(remote.config.model_dump(), keep_count=2))
    assert retain(remote, tmp_path / "work", now=now)["removed_count"] == 1
    assert all(
        point_key(remote.config, point.backup_id) in remote.client.objects for point in values[1:]
    )
