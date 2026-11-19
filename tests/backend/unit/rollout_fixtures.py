"""In-memory effects for controller failure/ordering tests; no claimed container proof."""

import copy
from dataclasses import dataclass

from tests.backend.unit.test_release_manifest import manifest_data
from tools.deploy.release_images import Registry
from tools.deploy.release_manifest import (
    Manifest,
    Spec,
    canonical,
    configuration,
    parse,
    proof,
    vendor_inventory,
)
from tools.deploy.rollout_controller import RolloutController
from tools.deploy.rollout_runtime import DatabaseIdentity, ReleaseRuntime
from tools.deploy.rollout_schema import EXPECTED, SchemaSnapshot
from tools.deploy.rollout_state import SMOKE_CHECKS, RolloutError, Selection
from tools.deploy.rollout_store import StateStore


def release(number=123, previous=None, *, backend=None, observability=False):
    data = manifest_data()
    data.update(
        release_id=f"release-{number}-1", previous_stable=previous, bootstrap=previous is None
    )
    data["provenance"]["run_id"] = number
    if backend is not None:
        data["images"]["backend"] = backend.model_dump()
    if observability:
        data["configuration"] = configuration("https", True).model_dump()
        data["vendor_images"] = vendor_inventory(True)
        data["images"]["collector"] = copy.deepcopy(data["images"]["backend"])
        data["services"].update(collector="collector", local_alert_receiver="backend")
    # Proofs are regenerated from the exact fixture candidate, not copied from a different release.
    candidate = {
        key: value
        for key, value in data.items()
        if key
        not in {"candidate_proof", "published_proof", "source_checks", "candidate_artifact_id"}
    }
    candidate["format"] = "prl-candidate"
    spec = parse(canonical(candidate), Spec)
    data.update(
        candidate_proof=proof(spec, "candidate").model_dump(),
        published_proof=proof(spec, "published").model_dump(),
    )
    return parse(canonical(data), Manifest)


def reference(number=1):
    return "ghcr.io/example/releases@sha256:" + f"{number:064x}"


class MemoryRegistry(Registry):
    def __init__(self):
        super().__init__()
        self.releases = {reference(): release()}
        self.missing = False

    def fetch(self, reference: str) -> Manifest:
        if reference not in self.releases:
            from tools.deploy.release_manifest import ReleaseError

            raise ReleaseError("missing-artifact")
        return self.releases[reference]

    def verify_images(self, spec, *, pull=False, complete=False):
        if self.missing:
            from tools.deploy.release_manifest import ReleaseError

            raise ReleaseError("missing-image")


class MemoryRuntime(ReleaseRuntime):
    def __init__(self, directory, store):
        super().__init__(["docker", "compose"], {}, "http://localhost:8080", "isolated", directory)
        self.store = store
        self.calls: list[str] = []
        self.fail: str | None = None
        self.fail_release: str | None = None
        self.database = DatabaseIdentity("a" * 64, "isolated_mysql_data")
        self.snapshot = SchemaSnapshot(
            tables=copy.deepcopy(EXPECTED),
            revisions=["20261113_1200_booking_quota"],
            unsupported_objects=0,
            nontransactional_tables=0,
        )
        self.previous_stable: Selection | None = None

    def validate_plan(self) -> dict:
        self.calls.append("plan")
        return {}

    def vendor_images(self) -> None:
        self.calls.append("images")

    def schema(self) -> SchemaSnapshot:
        return self.snapshot

    def prepare_database(self) -> DatabaseIdentity:
        return self.database

    def database_identity(self) -> DatabaseIdentity:
        return self.database

    def require_database(self, before: DatabaseIdentity) -> None:
        assert self.database == before

    def migrate(self) -> None:
        self.calls.append("migrate")
        if self.fails("migration"):
            raise RolloutError("migration-failed")

    def apply_grants(self) -> None:
        self.calls.append("grants")

    def rollout(self) -> None:
        self.calls.append("rollout")
        current = self.store.load()
        assert current is not None and current.stable == self.previous_stable
        if self.fails("rollout"):
            raise RolloutError("rollout-failed")

    def verify_images(self) -> None:
        self.calls.append("verify-images")

    def fails(self, phase: str) -> bool:
        return self.fail == phase and (
            self.fail_release is None
            or self.manifest is not None
            and self.manifest.release_id == self.fail_release
        )


@dataclass
class Fixture:
    control: RolloutController
    store: StateStore
    registry: MemoryRegistry
    runtime: MemoryRuntime

    def deploy(self, reference, *, resume=False):
        return self.control.deploy(reference, resume=resume)

    def rollback(self):
        return self.control.rollback()


def controller(tmp_path) -> Fixture:
    store = StateStore(tmp_path / "state")
    registry = MemoryRegistry()
    runtime = MemoryRuntime(tmp_path / "rendered", store)

    def smoke(host):
        runtime.calls.append("smoke")
        if runtime.fails("smoke"):
            raise RolloutError("smoke-failed")
        return sorted(SMOKE_CHECKS)

    control = RolloutController(
        store,
        registry,
        runtime,
        "b" * 40,
        "c" * 64,
        smoke=smoke,
        readiness=lambda host: runtime.calls.append("ready"),
    )
    return Fixture(control, store, registry, runtime)
