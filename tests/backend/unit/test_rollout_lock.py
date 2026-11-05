import os
import subprocess
import sys

import pytest
from tools.deploy.evidence import lifecycle_directory, lifecycle_lock
from tools.deploy.rollout_lock import operation_lock
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


def child(store, code):
    return subprocess.run(
        [sys.executable, "-c", code, str(store.directory)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_legacy_lifecycle_and_rollout_contend_on_same_lock(tmp_path):
    store = StateStore(lifecycle_directory(tmp_path))
    store.load()
    with lifecycle_lock(store.lock_path):
        result = child(
            store,
            """
import sys
from pathlib import Path
from tools.deploy.rollout_lock import operation_lock
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore
try:
    with operation_lock(StateStore(Path(sys.argv[1]))):
        raise SystemExit(9)
except RolloutError as e:
    print(e.reason)
""",
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "operation-locked"
    assert not result.stderr
    with operation_lock(store):
        pass


def test_crash_releases_kernel_ownership_without_deleting_lock_file(tmp_path):
    store = StateStore(tmp_path)
    result = child(
        store,
        """
import os,sys
from pathlib import Path
from tools.deploy.rollout_lock import operation_lock
from tools.deploy.rollout_store import StateStore
with operation_lock(StateStore(Path(sys.argv[1]))):
    os._exit(17)
""",
    )
    assert result.returncode == 17
    inode = store.lock_path.stat().st_ino
    with operation_lock(store):
        assert store.lock_path.stat().st_ino == inode


def test_operation_failures_are_not_misreported_as_lock_contention(tmp_path):
    store = StateStore(tmp_path)
    with pytest.raises(OSError, match="filesystem operation"):
        with operation_lock(store):
            raise OSError("filesystem operation")
    assert store.lock_path.exists()
    with operation_lock(store):
        pass


def test_lock_symlink_is_refused(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    target = tmp_path / "other"
    target.write_bytes(b"0")
    if os.name == "nt":
        monkeypatch.setattr(type(store.lock_path), "is_symlink", lambda p: p == store.lock_path)
    else:
        store.lock_path.symlink_to(target)
    with pytest.raises(RolloutError, match="invalid-state"):
        with operation_lock(store):
            pytest.fail("A redirected lock was acquired")
