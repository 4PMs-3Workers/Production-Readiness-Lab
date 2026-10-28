"""Serialize supported migration and backup lifecycles across implicit DDL commits."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class SchemaLifecycleBusy(Exception):
    """Another deployment/backup operation owns the database lifecycle."""


def lock_name(database: str) -> str:
    return "prl.schema." + hashlib.sha256(database.encode()).hexdigest()[:48]


@asynccontextmanager
async def schema_lifecycle(connection: AsyncConnection, timeout: int = 3) -> AsyncIterator[None]:
    database = (await connection.execute(text("SELECT DATABASE()"))).scalar_one()
    name = lock_name(database)
    acquired = (
        await connection.execute(
            text("SELECT GET_LOCK(:name, :timeout)"), {"name": name, "timeout": timeout}
        )
    ).scalar_one()
    if acquired != 1:
        raise SchemaLifecycleBusy()
    # Named locks survive COMMIT. Clear SQLAlchemy's metadata-query autobegin so
    # the caller owns a distinct business/migration/snapshot transaction.
    await connection.commit()
    try:
        yield
    finally:
        try:
            async with asyncio.timeout(5):
                await connection.rollback()
                await connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
                await connection.commit()
        except (SQLAlchemyError, TimeoutError):
            await connection.invalidate()


@asynccontextmanager
async def migration_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as connection:
        async with schema_lifecycle(connection):
            async with connection.begin():
                yield connection
            # The version-table commit finishes before releasing the named lock.
