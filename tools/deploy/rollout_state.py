"""Versioned host decisions; release publication and old evidence retain their contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tools.deploy.release_manifest import (
    SCHEMA,
    SHA,
    Digest,
    Hex,
    Manifest,
    Reference,
    Strict,
    canonical,
)

Phase = Literal[
    "preflight",
    "migration",
    "rollout",
    "readiness",
    "smoke",
    "succeeded",
    "rollback_preflight",
    "rollback_rollout",
    "rollback_readiness",
    "rollback_smoke",
    "rolled_back",
    "failed",
    "interrupted",
]
Reason = Literal[
    "invalid-state",
    "operation-locked",
    "interrupted-operation",
    "incompatible-configuration",
    "invalid-manifest",
    "missing-images",
    "schema-incompatible",
    "schema-partial",
    "database-unavailable",
    "image-mismatch",
    "volume-changed",
    "migration-failed",
    "rollout-failed",
    "readiness-failed",
    "smoke-failed",
    "rollback-unavailable",
    "rollback-incompatible",
    "rollback-failed",
    "deadline-exceeded",
    "unexpected-failure",
]
TERMINAL = {"succeeded", "rolled_back", "failed", "interrupted"}
SMOKE_CHECKS = {
    "frontend",
    "login",
    "history",
    "detail",
    "availability",
    "book",
    "book_replay",
    "cancel",
    "cancel_replay",
    "logout",
    "side_effects_exact",
}
NEXT: dict[str, set[str]] = {
    "preflight": {"migration"},
    "migration": {"rollout"},
    "rollout": {"readiness"},
    "readiness": {"smoke"},
    "smoke": {"succeeded", "rollback_preflight"},
    "rollback_preflight": {"rollback_rollout"},
    "rollback_rollout": {"rollback_readiness"},
    "rollback_readiness": {"rollback_smoke"},
    "rollback_smoke": {"rolled_back"},
}


class RolloutError(Exception):
    def __init__(self, reason: Reason):
        super().__init__(reason)
        self.reason = reason


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not value.endswith("Z") or instant.tzinfo != UTC:
        raise ValueError("Invalid UTC timeline")
    return instant


class Selection(Strict):
    reference: Reference
    payload_sha256: Hex
    release_id: Annotated[str, Field(pattern=r"^release-[1-9][0-9]*-[1-9][0-9]*$")]
    source_sha: SHA
    configuration_sha256: Hex
    images: dict[Literal["backend", "frontend", "collector"], Reference]
    image_ids: dict[Literal["backend", "frontend", "collector"], Digest]

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if set(self.images) != set(self.image_ids) or not {"backend", "frontend"} <= set(
            self.images
        ):
            raise ValueError("Invalid selected inventory")
        return self


def selection(reference: str, manifest: Manifest) -> Selection:
    return Selection(
        reference=reference,
        payload_sha256=hashlib.sha256(canonical(manifest)).hexdigest(),
        release_id=manifest.release_id,
        source_sha=manifest.provenance.source_sha,
        configuration_sha256=manifest.configuration.sha256,
        images={name: image.reference for name, image in manifest.images.items()},
        image_ids={name: image.image_id for name, image in manifest.images.items()},
    )


class Event(Strict):
    phase: Phase
    at: str
    elapsed_seconds: Annotated[float, Field(ge=0, le=3600, allow_inf_nan=False)]
    reason: Reason | None = None

    @model_validator(mode="after")
    def valid_time(self) -> Self:
        utc(self.at)
        if (self.phase in {"failed", "interrupted"}) != (self.reason is not None):
            raise ValueError("Invalid failure decision")
        return self


class Verification(Strict):
    schema_revision: str
    image_identity_verified: Literal[True]
    database_volume_preserved: Literal[True]
    worker_progress_verified: Literal[True]
    smoke_checks: list[str]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.schema_revision != SCHEMA or self.smoke_checks != sorted(SMOKE_CHECKS):
            raise ValueError("Incomplete service verification")
        return self


class Operation(Strict):
    operation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    command: Literal["deploy", "rollback"]
    orchestrator_sha: SHA
    target: Selection
    fallback: Selection | None = None
    events: list[Event] = Field(min_length=1, max_length=16)
    verification: Verification | None = None

    @property
    def phase(self) -> Phase:
        return self.events[-1].phase

    @model_validator(mode="after")
    def transitions(self) -> Self:
        if (self.phase in {"succeeded", "rolled_back"}) != (self.verification is not None):
            raise ValueError("A successful operation requires complete service proof")
        if self.events[0].phase != (
            "preflight" if self.command == "deploy" else "rollback_preflight"
        ):
            raise ValueError("Invalid initial transition")
        for before, after in zip(self.events, self.events[1:], strict=False):
            allowed = NEXT.get(before.phase, set()) | (
                {"failed", "interrupted"} if before.phase not in TERMINAL else set()
            )
            if after.phase not in allowed or after.elapsed_seconds < before.elapsed_seconds:
                raise ValueError("Invalid lifecycle transition")
            if utc(after.at) < utc(before.at):
                raise ValueError("Timeline moved backwards")
        if self.command == "deploy" and any(e.phase == "rollback_preflight" for e in self.events):
            if self.fallback is None:
                raise ValueError("Automatic rollback lacks a stable target")
        return self


class HostState(Strict):
    format: Literal["prl-rollout-state"] = "prl-rollout-state"
    format_version: Literal[1] = 1
    project: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
    configuration_sha256: Hex
    stable: Selection | None = None
    previous_stable: Selection | None = None
    operation: Operation | None = None

    @model_validator(mode="after")
    def stable_is_verified(self) -> Self:
        if self.previous_stable is not None and self.stable is None:
            raise ValueError("Previous release without a stable release")
        if self.operation is not None:
            op = self.operation
            verified = op.target if op.command == "rollback" else op.fallback
            if op.phase == "succeeded" and self.stable != op.target:
                raise ValueError("Unverified stable promotion")
            if op.phase == "rolled_back" and self.stable != verified:
                raise ValueError("Unverified rollback promotion")
        return self
