"""Private transaction intent for gateway smoke cleanup, excluded from public evidence."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator
from tools.deploy.release_manifest import Hex, ReleaseError, Strict, canonical, parse
from tools.deploy.rollout_state import RolloutError
from tools.deploy.rollout_store import StateStore


def binding(origin: str, email: str) -> str:
    return hashlib.sha256(
        canonical(dict(origin=origin.rstrip("/"), email=email.casefold()))
    ).hexdigest()


def utc_timestamp(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if len(value) > 64 or instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError("Invalid private transaction time")
    return value


class SmokePayload(Strict):
    purpose_id: UUID = Field(repr=False)
    staff_id: UUID = Field(repr=False)
    starts_at: str = Field(repr=False)

    _utc_start = field_validator("starts_at")(utc_timestamp)


class SmokeReceipt(Strict):
    id: UUID = Field(repr=False)
    status: Literal["UPCOMING"]
    created_at: str = Field(repr=False)

    _utc_created = field_validator("created_at")(utc_timestamp)


class SmokeTransaction(Strict):
    format: Literal["prl-private-smoke"] = "prl-private-smoke"
    format_version: Literal[1] = 1
    account_sha256: Hex = Field(repr=False)
    key: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", repr=False)]
    payload: SmokePayload = Field(repr=False)
    receipt: SmokeReceipt | None = Field(default=None, repr=False)


class SmokeJournal:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._pending: SmokeTransaction | None = None
        self._files = StateStore(path.parent) if path is not None else None

    def load(self, account_sha256: str) -> SmokeTransaction | None:
        try:
            pending = self._pending
            if self.path is not None:
                assert self._files is not None
                self._files._directory(self.path.parent)
                if self.path.is_symlink():
                    raise RolloutError("invalid-state")
                pending = None
                if self.path.exists():
                    if os.name != "nt" and self.path.stat().st_mode & 0o077:
                        raise RolloutError("invalid-state")
                    with self.path.open("rb") as handle:
                        raw = handle.read(8193)
                    if len(raw) > 8192:
                        raise RolloutError("invalid-state")
                    pending = parse(raw, SmokeTransaction)
            if pending is not None and pending.account_sha256 != account_sha256:
                raise RolloutError("smoke-cleanup-failed")
            return pending
        except (OSError, ReleaseError):
            raise RolloutError("invalid-state") from None

    def _save(self, pending: SmokeTransaction) -> None:
        try:
            if self.path is not None:
                assert self._files is not None
                self._files._write(self.path, pending)
            self._pending = pending
        except (OSError, ReleaseError):
            raise RolloutError("invalid-state") from None

    def begin(self, account_sha256: str, key: str, payload: dict) -> SmokeTransaction:
        if self.load(account_sha256) is not None:
            raise RolloutError("smoke-cleanup-failed")
        try:
            pending = parse(
                canonical(dict(account_sha256=account_sha256, key=key, payload=payload)),
                SmokeTransaction,
            )
        except ReleaseError:
            raise RolloutError("invalid-state") from None
        self._save(pending)
        return pending

    def received(self, account_sha256: str, receipt: dict) -> SmokeTransaction:
        pending = self.load(account_sha256)
        if pending is None:
            raise RolloutError("invalid-state")
        try:
            received = parse(canonical(receipt), SmokeReceipt)
            if pending.receipt is not None and received != pending.receipt:
                raise RolloutError("smoke-cleanup-failed")
            updated = parse(
                canonical(
                    dict(pending.model_dump(mode="json"), receipt=received.model_dump(mode="json"))
                ),
                SmokeTransaction,
            )
        except ReleaseError:
            raise RolloutError("smoke-failed") from None
        self._save(updated)
        return updated

    def cleared(self, account_sha256: str, appointment: str) -> None:
        pending = self.load(account_sha256)
        if pending is None or pending.receipt is None or str(pending.receipt.id) != appointment:
            raise RolloutError("invalid-state")
        try:
            if self.path is not None:
                self.path.unlink()
                if os.name != "nt":
                    descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            self._pending = None
        except OSError:
            raise RolloutError("invalid-state") from None
