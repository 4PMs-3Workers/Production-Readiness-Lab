"""Use the existing crash-released kernel lock for both deployment directions."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager

from tools.deploy.evidence import lifecycle_lock
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


@contextmanager
def operation_lock(store: StateStore) -> Iterator[None]:
    store.load()  # Validate the state directory before opening its lock file.
    if store.lock_path.is_symlink():
        raise RolloutError("invalid-state")
    with ExitStack() as resources:
        try:
            resources.enter_context(lifecycle_lock(store.lock_path))
        except OSError:
            raise RolloutError("operation-locked") from None
        yield
