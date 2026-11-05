"""Atomic authoritative host state and immutable, digest-addressed manifest cache."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from tools.deploy.release_manifest import (
    Manifest,
    RegistryReference,
    ReleaseError,
    Strict,
    canonical,
    read,
    write,
)
from tools.deploy.rollout_state import HostState, RolloutError, Selection, selection


class StateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "release-state.json"
        self.lock_path = directory / ".lifecycle.lock"

    def _directory(self, path: Path) -> None:
        if self.directory.is_symlink() or any(
            candidate.is_symlink() for candidate in (path, *path.parents)
        ):
            raise RolloutError("invalid-state")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _write(self, path: Path, value: Strict) -> None:
        self._directory(path.parent)
        if path.is_symlink():
            raise RolloutError("invalid-state")
        write(path, value)
        # fsync the replaced directory entry too; file fsync alone is insufficient
        # for a stable-pointer commit across a Linux host power loss.
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def load(self) -> HostState | None:
        self._directory(self.directory)
        if self.path.is_symlink():
            raise RolloutError("invalid-state")
        if not self.path.exists():
            return None
        try:
            return read(self.path, HostState)
        except ReleaseError:
            raise RolloutError("invalid-state") from None

    def save(self, state: HostState) -> None:
        # Retirement is not a second authoritative commit. Content addressing
        # retains every prepared snapshot if the subsequent state replace fails;
        # later refusals/interruptions can archive their own distinct snapshot.
        prior = self.load()
        if prior is not None:
            if (prior.project, prior.configuration_sha256) != (
                state.project,
                state.configuration_sha256,
            ):
                raise RolloutError("incompatible-configuration")
            if prior.operation is not None and (
                state.operation is None
                or prior.operation.operation_id != state.operation.operation_id
            ):
                digest = hashlib.sha256(canonical(prior)).hexdigest()
                archive = (
                    self.directory / "operations" / prior.operation.operation_id / f"{digest}.json"
                )
                self._directory(archive.parent)
                if archive.is_symlink():
                    raise RolloutError("invalid-state")
                if archive.exists():
                    if read(archive, HostState) != prior:
                        raise RolloutError("invalid-state")
                else:
                    self._write(archive, prior)
        self._write(self.path, state)

    def _manifest_path(self, reference: str) -> Path:
        RegistryReference(reference=reference)
        key = hashlib.sha256(reference.encode("ascii")).hexdigest()
        return self.directory / "manifests" / f"{key}.json"

    def remember(self, reference: str, manifest: Manifest) -> Selection:
        path = self._manifest_path(reference)
        if path.exists():
            if path.is_symlink() or read(path, Manifest) != manifest:
                raise RolloutError("invalid-state")
        else:
            self._write(path, manifest)
        return selection(reference, manifest)

    def manifest(self, selected: Selection) -> Manifest:
        path = self._manifest_path(selected.reference)
        try:
            if path.is_symlink():
                raise RolloutError("invalid-state")
            manifest = read(path, Manifest)
        except ReleaseError:
            raise RolloutError("rollback-unavailable") from None
        if selection(selected.reference, manifest) != selected:
            raise RolloutError("invalid-state")
        return manifest

    def export(self, destination: Path) -> None:
        state = self.load()
        if state is None:
            raise RolloutError("invalid-state")
        # Same strict, payload-free schema as the authoritative decision record.
        self._write(destination, state)


def replace_state(state: HostState, **changes) -> HostState:
    """model_copy(update=...) bypasses Pydantic validation; decisions must not."""
    from tools.deploy.release_manifest import parse

    return parse(canonical(dict(state.model_dump(mode="json"), **changes)), HostState)
