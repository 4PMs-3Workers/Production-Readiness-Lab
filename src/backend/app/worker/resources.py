"""Worker-owned engines and a read-only, release-specific schema gate."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.transactions import read_transaction

SUPPORTED_SCHEMA_REVISIONS = frozenset({"20261019_1200_proposal_replay"})


class SchemaCompatibilityError(Exception):
    """The database revision is not accepted by both this worker and its release settings."""


async def validate_schema(db: AsyncSession, settings: Settings) -> None:
    """Read only; the caller owns the transaction and its deadline."""
    revisions = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    if (
        len(revisions) != 1
        or revisions[0] not in SUPPORTED_SCHEMA_REVISIONS
        or revisions[0] not in settings.accepted_schema_revisions
    ):
        raise SchemaCompatibilityError()


@dataclass(frozen=True)
class WorkerResources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def check_compatibility(self, settings: Settings) -> None:
        async with self.sessions() as db:
            async with read_transaction(db, settings):
                await validate_schema(db, settings)


@asynccontextmanager
async def open_resources(
    settings: Settings, *, engine_factory: Callable[..., AsyncEngine] = create_database_engine
) -> AsyncIterator[WorkerResources]:
    # This executes inside the worker's event loop, never during module import.
    engine = engine_factory(
        settings.database_url,
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
    )
    try:
        yield WorkerResources(engine, async_sessionmaker(engine, expire_on_commit=False))
    finally:
        async with asyncio.timeout(settings.worker_grace_seconds):
            await engine.dispose()
