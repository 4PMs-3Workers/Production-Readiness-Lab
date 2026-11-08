"""Resolve reviewed immutable releases and verify artifacts before touching services."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tools.deploy.release_manifest import ROOT, Manifest, ReleaseError, require_compatible
from tools.deploy.rollout_state import RolloutError, Selection, selection
from tools.deploy.rollout_store import StateStore


@dataclass(frozen=True)
class Prepared:
    selected: Selection
    manifest: Manifest


class ReleaseRegistry(Protocol):
    def fetch(self, reference: str, /) -> Manifest: ...

    def verify_images(self, spec: Manifest, /, *, pull: bool) -> None: ...


class ReleaseConsumer:
    def __init__(self, registry: ReleaseRegistry, store: StateStore, *, root: Path = ROOT):
        self.registry, self.store = registry, store
        self.root = root

    def resolve(self, reference: str) -> Prepared:
        try:
            manifest = self.registry.fetch(reference)
            return Prepared(selection(reference, manifest), manifest)
        except ReleaseError as error:
            if error.code == "deadline-exceeded":
                raise RolloutError("deadline-exceeded") from None
            raise RolloutError("invalid-manifest") from None

    def prepare(self, reference: str, *, expected: Selection | None = None) -> Prepared:
        return self.verify(self.resolve(reference), expected=expected)

    def verify(self, prepared: Prepared, *, expected: Selection | None = None) -> Prepared:
        manifest, selected = prepared.manifest, prepared.selected
        try:
            require_compatible(manifest, self.root, allow_previous=expected is not None)
            if expected is not None and selected != expected:
                raise RolloutError("invalid-manifest")
        except ReleaseError as error:
            if error.code == "incompatible-configuration":
                raise RolloutError("incompatible-configuration") from None
            if error.code == "incompatible-schema":
                raise RolloutError("schema-incompatible") from None
            if error.code == "deadline-exceeded":
                raise RolloutError("deadline-exceeded") from None
            raise RolloutError("invalid-manifest") from None
        try:
            # The publishing contract checks both registry bytes and local image
            # IDs after pulling. Same-source rebuilds are not interchangeable.
            self.registry.verify_images(manifest, pull=True)
        except ReleaseError as error:
            if error.code == "deadline-exceeded":
                raise RolloutError("deadline-exceeded") from None
            raise RolloutError("missing-images") from None
        self.store.remember(selected.reference, manifest)
        return Prepared(selected, manifest)
