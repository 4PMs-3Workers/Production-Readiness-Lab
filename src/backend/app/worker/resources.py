"""Worker-owned engines and a read-only, release-specific schema gate."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.transactions import mysql_error_code, read_transaction

SUPPORTED_SCHEMA_REVISIONS = frozenset({"20261019_1200_proposal_replay"})
CleanupDeadline = Callable[[], float | None]


class SchemaCompatibilityError(Exception):
    """The database revision is not accepted by both this worker and its release settings."""


class ResourceCleanupError(Exception):
    """Worker resources could not finish closing inside the shared shutdown budget."""


def earlier_deadline(deadline: float, provider: CleanupDeadline | None) -> float:
    limit = provider() if provider is not None else None
    return min(deadline, limit) if limit is not None else deadline


async def validate_schema(db: AsyncSession, settings: Settings) -> None:
    """Read only; the caller owns the transaction and its deadline."""
    try:
        revisions = (
            (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
        )
    except SQLAlchemyError as error:
        if mysql_error_code(error) == 1146:
            raise SchemaCompatibilityError() from None
        raise
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
    settings: Settings,
    *,
    engine_factory: Callable[..., AsyncEngine] = create_database_engine,
    cleanup_deadline: CleanupDeadline | None = None,
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
        deadline = earlier_deadline(
            asyncio.get_running_loop().time() + settings.worker_grace_seconds, cleanup_deadline
        )
        try:
            async with asyncio.timeout_at(deadline):
                await engine.dispose()
        except (SQLAlchemyError, TimeoutError):
            # Public synchronous close=False only detaches the pool; it performs no network I/O.
            engine.sync_engine.dispose(close=False)
            raise ResourceCleanupError() from None
