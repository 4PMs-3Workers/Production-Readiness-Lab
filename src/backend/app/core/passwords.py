"""Reusable Argon2id policy with bounded, cancellation-safe worker offload."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import anyio
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

T = TypeVar("T")


@dataclass(frozen=True)
class PasswordPolicy:
    memory_cost_kib: int = 65536
    time_cost: int = 3
    parallelism: int = 1


class PasswordService:
    def __init__(self, policy: PasswordPolicy, max_concurrency: int) -> None:
        self._hasher = PasswordHasher(
            memory_cost=policy.memory_cost_kib,
            time_cost=policy.time_cost,
            parallelism=policy.parallelism,
            type=Type.ID,
        )
        self._limiter = anyio.CapacityLimiter(max_concurrency)
        self._dummy = ""

    @classmethod
    async def create(cls, policy: PasswordPolicy, max_concurrency: int) -> "PasswordService":
        service = cls(policy, max_concurrency)
        service._dummy = await service.hash("unused-dummy-verification-password")
        return service

    async def _run(self, operation: Callable[[], T]) -> T:
        task = asyncio.create_task(anyio.to_thread.run_sync(operation, limiter=self._limiter))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancelled request must not release a slot while Argon2 still runs.
            await task
            raise

    async def hash(self, password: str) -> str:
        return await self._run(lambda: self._hasher.hash(password))

    async def verify(self, password: str, encoded_hash: str) -> bool:
        def check() -> bool:
            try:
                return self._hasher.verify(encoded_hash, password)
            except (VerificationError, InvalidHashError):
                return False

        return await self._run(check)

    async def verify_dummy(self, password: str) -> None:
        await self.verify(password, self._dummy)

    def needs_rehash(self, encoded_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded_hash)
        except InvalidHashError:
            return True
