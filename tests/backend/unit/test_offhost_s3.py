import hashlib

import pytest
from tests.backend.unit.offhost_fixtures import Store, client_error
from tests.backend.unit.test_offhost_contract import configuration
from tools.backup.offhost_contract import Code, RemoteError
from tools.backup.offhost_io import Deadline
from tools.backup.offhost_s3 import S3


def adapter():
    store = Store()
    return S3(configuration(), Deadline(5), client=store), store


def test_atomic_upload_and_private_download_are_duplicate_safe(tmp_path):
    remote, store = adapter()
    value, path = b"encrypted-private-ciphertext", tmp_path / "encrypted"
    path.write_bytes(value)
    path.chmod(0o600)
    sha = hashlib.sha256(value).hexdigest()
    key = remote.config.scope + "points/owned/blob.age"
    remote.upload(key, path, sha, len(value))
    with pytest.raises(RemoteError, match=Code.CONFLICT.value):
        remote.upload(key, path, sha, len(value))
    remote.download(key, tmp_path / "download", sha, len(value))
    assert (tmp_path / "download").read_bytes() == value
    assert store.objects[key] == value


@pytest.mark.parametrize("operation", ["get", "put", "download", "delete"])
def test_unrelated_prefix_is_refused_before_any_store_request(tmp_path, operation):
    remote, store = adapter()
    with pytest.raises(RemoteError, match=Code.IDENTITY.value):
        if operation == "get":
            remote.get("unrelated/keep")
        elif operation == "put":
            remote.put("unrelated/keep", b"x")
        elif operation == "delete":
            remote.delete("unrelated/keep")
        else:
            remote.download("unrelated/keep", tmp_path / "out", "a" * 64, 1)
    assert not store.calls


def test_retry_is_bounded_and_403_is_never_treated_as_missing(monkeypatch):
    remote, store = adapter()
    delays = []
    monkeypatch.setattr("tools.backup.offhost_s3.time.sleep", delays.append)
    key = remote.config.scope + "points/owned/complete.json"
    store.failures = [client_error(503), client_error(429)]
    remote.put(key, b"complete")
    result = remote.get(key)
    assert result is not None and len(delays) == 2 and result[0] == b"complete"
    store.failures = [client_error(403)]
    with pytest.raises(RemoteError, match=Code.TRANSPORT.value):
        remote.get(key)
    store.failures = [client_error(503)] * 3
    with pytest.raises(RemoteError):
        remote.get(key)
    assert not store.failures


def test_mismatch_and_expired_deadline_leave_no_download(tmp_path):
    remote, store = adapter()
    key = remote.config.scope + "points/owned/blob.age"
    store.objects[key] = b"corrupt"
    target = tmp_path / "download"
    with pytest.raises(RemoteError, match=Code.INTEGRITY.value):
        remote.download(key, target, "a" * 64, 7)
    assert not target.exists()
    remote.deadline = Deadline(0)
    with pytest.raises(RemoteError, match=Code.TIMEOUT.value):
        remote.get(key)
