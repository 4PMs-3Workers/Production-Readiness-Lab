"""Inspect the reviewed schema under a backup/DDL lifecycle lock."""

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.backup.config import BackupConfig, BackupError, Reason, identifier
from app.core.database import create_database_engine
from app.core.schema_lifecycle import SchemaLifecycleBusy, schema_lifecycle
from app.models import Base


def migration_contract() -> tuple[str, str]:
    folder = Path(__file__).resolve().parents[2] / "migrations"
    config = Config()
    config.set_main_option("script_location", str(folder))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise BackupError(Reason.INCOMPATIBLE)
    digest = hashlib.sha256()
    for path in sorted((folder / "versions").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return heads[0], digest.hexdigest()


@dataclass(frozen=True)
class TableSchema:
    name: str
    ddl: str
    columns: tuple[str, ...]
    writable: tuple[str, ...]
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class SourceSchema:
    revision: str
    migration_sha256: str
    server_uuid: str
    server_version: str
    charset: str
    collation: str
    database: str
    tables: tuple[TableSchema, ...]


async def require_metadata_visibility(db: AsyncConnection) -> None:
    """An empty filtered catalog is not evidence that unsupported objects are absent.

    Support explicit direct grants. Refuse role-only or partially revoked grant
    configurations rather than infer metadata visibility from an empty result.
    """
    database = (await db.execute(text("SELECT DATABASE()"))).scalar_one()
    grants = (await db.execute(text("SHOW GRANTS FOR CURRENT_USER"))).scalars().all()
    global_privileges: set[str] = set()
    schema_privileges: set[str] = set()
    for grant in grants:
        if grant.startswith("REVOKE "):
            raise BackupError(Reason.UNSUPPORTED)
        match = re.match(r"GRANT (.+?) ON (.+?) TO ", grant)
        if match is None:
            continue
        privileges = {value.strip() for value in match[1].split(",")}
        if match[2] == "*.*":
            global_privileges.update(privileges)
        if match[2] == f"`{database}`.*":
            schema_privileges.update(privileges)
    visible = global_privileges | schema_privileges
    if ("ALL PRIVILEGES" not in visible and not {"TRIGGER", "EVENT"} <= visible) or not (
        {"ALL PRIVILEGES", "SELECT", "SHOW_ROUTINE"} & global_privileges
    ):
        raise BackupError(Reason.UNSUPPORTED)


async def read_schema(db: AsyncConnection) -> SourceSchema:
    name, version, server, zone = (
        await db.execute(text("SELECT DATABASE(), VERSION(), @@server_uuid, @@session.time_zone"))
    ).one()
    if not version.startswith("8.4.") or zone != "+00:00":
        raise BackupError(Reason.UNSUPPORTED)
    UUID(server)
    revision, contract = migration_contract()
    versions = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    if versions != [revision]:
        raise BackupError(Reason.INCOMPATIBLE)
    tables = (
        await db.execute(
            text(
                "SELECT TABLE_NAME, ENGINE, TABLE_TYPE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME"
            )
        )
    ).all()
    expected = set(Base.metadata.tables) | {"alembic_version"}
    if {row[0] for row in tables} != expected or any(
        engine != "InnoDB" or kind != "BASE TABLE" for _, engine, kind in tables
    ):
        raise BackupError(Reason.UNSUPPORTED)
    for catalog, field in (
        ("TRIGGERS", "TRIGGER_SCHEMA"),
        ("ROUTINES", "ROUTINE_SCHEMA"),
        ("EVENTS", "EVENT_SCHEMA"),
    ):
        if (
            await db.execute(
                text(f"SELECT COUNT(*) FROM information_schema.{catalog} WHERE {field}=DATABASE()")
            )
        ).scalar_one():
            raise BackupError(Reason.UNSUPPORTED)
    charset, collation = (
        await db.execute(
            text(
                "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                "FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME=DATABASE()"
            )
        )
    ).one()
    result = []
    for table, _, _ in tables:
        ddl = (await db.execute(text(f"SHOW CREATE TABLE {identifier(table)}"))).one()[1]
        columns = (
            await db.execute(
                text(
                    "SELECT COLUMN_NAME, GENERATION_EXPRESSION FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY ORDINAL_POSITION"
                ),
                {"table": table},
            )
        ).all()
        names = tuple(column for column, _ in columns)
        expected_columns = (
            {"version_num"}
            if table == "alembic_version"
            else set(Base.metadata.tables[table].columns.keys())
        )
        if set(names) != expected_columns:
            raise BackupError(Reason.UNSUPPORTED)
        keys = tuple(
            (
                await db.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table "
                        "AND INDEX_NAME='PRIMARY' "
                        "ORDER BY SEQ_IN_INDEX"
                    ),
                    {"table": table},
                )
            )
            .scalars()
            .all()
        )
        if not keys:
            raise BackupError(Reason.UNSUPPORTED)
        for column in names:
            identifier(column)
        result.append(
            TableSchema(
                table,
                ddl,
                names,
                tuple(column for column, expression in columns if not expression),
                keys,
            )
        )
    return SourceSchema(
        revision, contract, server, version, charset, collation, name, tuple(result)
    )


@asynccontextmanager
async def open_source(config: BackupConfig) -> AsyncIterator[tuple[AsyncConnection, SourceSchema]]:
    engine = create_database_engine(
        config.database_url,
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 5},
    )
    try:
        async with engine.connect() as db:
            async with schema_lifecycle(db, config.lock_seconds):
                locked = False
                try:
                    async with asyncio.timeout(config.query_seconds):
                        await db.execute(
                            text(f"SET SESSION lock_wait_timeout={config.lock_seconds}")
                        )
                        await db.execute(text("LOCK INSTANCE FOR BACKUP"))
                        locked = True
                        await require_metadata_visibility(db)
                        schema = await read_schema(db)
                        await db.rollback()
                    yield db, schema
                finally:
                    if locked:
                        try:
                            async with asyncio.timeout(5):
                                await db.rollback()
                                await db.execute(text("UNLOCK INSTANCE"))
                                await db.commit()
                        except (SQLAlchemyError, TimeoutError):
                            await db.invalidate()
    except SchemaLifecycleBusy:
        raise BackupError(Reason.BUSY) from None
    except TimeoutError:
        raise BackupError(Reason.TIMEOUT) from None
    except SQLAlchemyError:
        raise BackupError(Reason.DATABASE) from None
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()
