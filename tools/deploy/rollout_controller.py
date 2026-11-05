"""Serialized deployment decisions; stable promotion follows complete live verification."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal
from uuid import uuid4

from tools.deploy.release_images import Registry
from tools.deploy.release_manifest import SCHEMA, canonical, parse
from tools.deploy.rollout_lock import operation_lock
from tools.deploy.rollout_preflight import ReleaseConsumer
from tools.deploy.rollout_readiness import ready
from tools.deploy.rollout_runtime import Deadline, ReleaseRuntime
from tools.deploy.rollout_schema import require_schema
from tools.deploy.rollout_state import (
    TERMINAL,
    Event,
    HostState,
    Operation,
    Phase,
    Reason,
    Refusal,
    RolloutError,
    Selection,
    Verification,
    timestamp,
)
from tools.deploy.rollout_store import StateStore, replace_state


class RolloutController:
    def __init__(
        self,
        store: StateStore,
        registry: Registry,
        runtime: ReleaseRuntime,
        orchestrator_sha: str,
        configuration_sha256: str,
        *,
        smoke: Callable[[ReleaseRuntime], list[str]],
        readiness: Callable[[ReleaseRuntime], None] = ready,
    ):
        self.store, self.registry, self.runtime = store, registry, runtime
        self.source_sha, self.configuration_sha256 = orchestrator_sha, configuration_sha256
        self.consumer = ReleaseConsumer(registry, store)
        self.smoke, self.readiness = smoke, readiness
        self.state: HostState | None = None
        self.started = 0.0
        self.started_at = ""

    def _budget(self, seconds: float) -> None:
        budget = Deadline(seconds)
        self.runtime.deadline = budget
        self.registry.deadline = budget.expires

    def _host(self) -> HostState:
        state = self.store.load() or HostState(
            project=self.runtime.project, configuration_sha256=self.configuration_sha256
        )
        if (state.project, state.configuration_sha256) != (
            self.runtime.project,
            self.configuration_sha256,
        ):
            raise RolloutError("incompatible-configuration")
        if state.operation is not None and state.operation.phase not in TERMINAL:
            raise RolloutError("interrupted-operation")
        return state

    def _begin(
        self,
        state: HostState,
        selected: Selection,
        command: Literal["deploy", "rollback"] = "deploy",
    ) -> None:
        op = Operation(
            operation_id=uuid4().hex,
            command=command,
            orchestrator_sha=self.source_sha,
            target=selected,
            events=[
                Event(
                    phase="preflight" if command == "deploy" else "rollback_preflight",
                    at=self.started_at,
                    elapsed_seconds=0.0,
                )
            ],
        )
        self.state = replace_state(state, operation=op.model_dump(mode="json"), refusal=None)
        self.store.save(self.state)

    def _transition(
        self,
        phase: Phase,
        *,
        reason: Reason | None = None,
        verification: Verification | None = None,
        operation_changes: dict | None = None,
        **changes,
    ) -> HostState:
        assert self.state is not None and self.state.operation is not None
        data = self.state.operation.model_dump(mode="json")
        data.update(operation_changes or {})
        data["events"].append(
            Event(
                phase=phase,
                at=timestamp(),
                elapsed_seconds=time.monotonic() - self.started,
                reason=reason,
            ).model_dump(mode="json")
        )
        data["verification"] = verification.model_dump(mode="json") if verification else None
        operation = parse(canonical(data), Operation)
        state = replace_state(self.state, operation=operation.model_dump(mode="json"), **changes)
        self.store.save(state)
        self.state = state
        return state

    def _proof(self, checks: list[str]) -> Verification:
        return Verification(
            schema_revision=SCHEMA,
            image_identity_verified=True,
            database_volume_preserved=True,
            worker_progress_verified=True,
            smoke_checks=checks,
        )

    def _failed(self, error: Exception) -> HostState:
        reason: Reason = error.reason if isinstance(error, RolloutError) else "unexpected-failure"
        return self._transition("failed", reason=reason)

    def _refuse(
        self, state: HostState, command: Literal["deploy", "rollback"], reason: Reason
    ) -> HostState:
        refusal = Refusal(command=command, at=self.started_at, reason=reason)
        self.state = replace_state(state, refusal=refusal.model_dump(mode="json"))
        self.store.save(self.state)
        return self.state

    def deploy(self, reference: str) -> HostState:
        with operation_lock(self.store):
            self._budget(600)
            self.started, self.started_at = time.monotonic(), timestamp()
            state = self._host()
            try:
                prepared = self.consumer.resolve(reference)
            except RolloutError as error:
                return self._refuse(state, "deploy", error.reason)
            self._begin(state, prepared.selected)
            try:
                if state.stable is None:
                    if not prepared.manifest.bootstrap:
                        raise RolloutError("invalid-manifest")
                elif (
                    prepared.manifest.bootstrap
                    or prepared.manifest.previous_stable != state.stable.reference
                ):
                    raise RolloutError("invalid-manifest")
                self.consumer.verify(prepared)
                self.runtime.select(prepared.manifest)
                self.runtime.validate_plan()
                self.runtime.vendor_images()
                require_schema(
                    self.runtime.schema(),
                    prepared.manifest.accepted_schema_revisions,
                    allow_empty=state.stable is None,
                )
                self._transition("migration")
                before = self.runtime.prepare_database()
                require_schema(
                    self.runtime.schema(),
                    prepared.manifest.accepted_schema_revisions,
                    allow_empty=state.stable is None,
                )
                self.runtime.migrate()
                self.runtime.apply_grants()
                require_schema(self.runtime.schema(), prepared.manifest.accepted_schema_revisions)
                self.runtime.require_database(before)
                self._transition("rollout")
                self.runtime.rollout()
                self.runtime.require_database(before)
                self._transition("readiness")
                self.readiness(self.runtime)
                self.runtime.verify_images()
                self._transition("smoke")
                checks = self.smoke(self.runtime)
                require_schema(self.runtime.schema(), prepared.manifest.accepted_schema_revisions)
                self.runtime.verify_images()
                self.runtime.require_database(before)
                self.runtime.deadline.remaining()
                return self._transition(
                    "succeeded",
                    verification=self._proof(checks),
                    stable=prepared.selected.model_dump(mode="json"),
                    previous_stable=state.stable.model_dump(mode="json") if state.stable else None,
                )
            except Exception as error:
                assert self.state is not None and self.state.operation is not None
                if state.stable is not None and self.state.operation.phase in {
                    "rollout",
                    "readiness",
                    "smoke",
                }:
                    return self._automatic_restore(state.stable, error)
                return self._failed(error)

    def _automatic_restore(self, target: Selection, error: Exception) -> HostState:
        reason: Reason = error.reason if isinstance(error, RolloutError) else "unexpected-failure"
        # This is the measured decision. The existing operation/lock stays owned,
        # while every registry, Compose and HTTP call now shares this 300s budget.
        self._budget(300)
        self._transition(
            "rollback_preflight",
            operation_changes={
                "fallback": target.model_dump(mode="json"),
                "trigger_reason": reason,
            },
        )
        try:
            return self._restore(target)
        except Exception as rollback_error:
            return self._failed(rollback_error)

    def _restore(self, target: Selection) -> HostState:
        prepared = self.consumer.prepare(target.reference, expected=target)
        self.runtime.select(prepared.manifest)
        self.runtime.validate_plan()
        self.runtime.vendor_images()
        require_schema(self.runtime.schema(), prepared.manifest.accepted_schema_revisions)
        before = self.runtime.database_identity()
        self._transition("rollback_rollout")
        # There is deliberately no migration, seed, downgrade, or DB startup here.
        self.runtime.rollout()
        self.runtime.require_database(before)
        self._transition("rollback_readiness")
        self.readiness(self.runtime)
        self.runtime.verify_images()
        self._transition("rollback_smoke")
        checks = self.smoke(self.runtime)
        require_schema(self.runtime.schema(), prepared.manifest.accepted_schema_revisions)
        self.runtime.verify_images()
        self.runtime.require_database(before)
        self.runtime.deadline.remaining()
        assert self.state is not None and self.state.operation is not None
        previous = self.state.previous_stable if self.state.operation.command == "deploy" else None
        return self._transition(
            "rolled_back",
            verification=self._proof(checks),
            stable=target.model_dump(mode="json"),
            previous_stable=previous.model_dump(mode="json") if previous else None,
        )

    def rollback(self) -> HostState:
        with operation_lock(self.store):
            self._budget(300)
            self.started, self.started_at = time.monotonic(), timestamp()
            state = self._host()
            target = state.previous_stable
            if (
                state.operation is not None
                and state.operation.command == "deploy"
                and state.operation.phase == "failed"
                and any(
                    e.phase in {"migration", "rollout", "readiness", "smoke"}
                    for e in state.operation.events
                )
            ):
                target = state.stable
            if target is None:
                return self._refuse(state, "rollback", "rollback-unavailable")
            self._begin(state, target, "rollback")
            try:
                return self._restore(target)
            except Exception as error:
                return self._failed(error)
