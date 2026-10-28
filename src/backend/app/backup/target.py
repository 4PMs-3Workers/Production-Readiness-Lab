"""Explicit empty-target guard; recovery never replaces the current/source schema."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.backup.config import BackupConfig, BackupError, Reason, database_name, identifier
from app.backup.manifest import Manifest
from app.core.database import create_database_engine
from app.core.schema_lifecycle import SchemaLifecycleBusy, schema_lifecycle


@asynccontextmanager
async def empty_target(
    config: BackupConfig, manifest: Manifest, target_database: str, *, require_empty: bool = True
) -> AsyncIterator[AsyncConnection]:
    identifier(target_database)
    # Supported rehearsal workflow always requires a distinct name, even on an
    # isolated server. There is intentionally no overwrite/force escape hatch.
    if (
        config.target_url is None
        or target_database != database_name(config.target_url)
        or target_database.casefold()
        in {manifest.source.database.casefold(), database_name(config.database_url).casefold()}
    ):
        raise BackupError(Reason.WRONG_TARGET)
    engine = create_database_engine(
        config.target_url,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 5},
    )
    try:
        async with engine.connect() as db:
            async with schema_lifecycle(db, config.lock_seconds):
                async with asyncio.timeout(config.query_seconds):
                    name, version, zone = (
                        await db.execute(text("SELECT DATABASE(), VERSION(), @@session.time_zone"))
                    ).one()
                    if (
                        name != target_database
                        or not version.startswith("8.4.")
                        or zone != "+00:00"
                    ):
                        raise BackupError(Reason.WRONG_TARGET)
                    charset, collation = (
                        await db.execute(
                            text(
                                "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                                "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()"
                            )
                        )
                    ).one()
                    if (charset, collation) != (manifest.source.charset, manifest.source.collation):
                        raise BackupError(Reason.INCOMPATIBLE)
                    for catalog, field in (
                        ("TABLES", "TABLE_SCHEMA"),
                        ("ROUTINES", "ROUTINE_SCHEMA"),
                        ("EVENTS", "EVENT_SCHEMA"),
                    ):
                        count = (
                            await db.execute(
                                text(
                                    f"SELECT COUNT(*) FROM information_schema.{catalog} "
                                    f"WHERE {field}=DATABASE()"
                                )
                            )
                        ).scalar_one()
                        if require_empty and count:
                            raise BackupError(Reason.NONEMPTY)
                    await db.execute(text(f"SET SESSION lock_wait_timeout={config.lock_seconds}"))
                    await db.rollback()
                yield db
    except SchemaLifecycleBusy:
        raise BackupError(Reason.BUSY) from None
    except TimeoutError:
        raise BackupError(Reason.TIMEOUT) from None
    except SQLAlchemyError:
        raise BackupError(Reason.DATABASE) from None
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()
