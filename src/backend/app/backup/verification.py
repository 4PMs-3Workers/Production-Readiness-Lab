"""Verify the restored artifact, not changing live-source counts."""

import hashlib
import re
import time
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.backup.config import BackupError, Reason, identifier
from app.backup.manifest import Manifest
from app.backup.schema import read_schema
from app.backup.snapshot import row_records


def normalized_ddl(ddl: str) -> str:
    # AUTO_INCREMENT is a live counter, not a constraint/index/schema definition.
    return re.sub(r"\sAUTO_INCREMENT=\d+", "", ddl)


async def foreign_keys(db: AsyncConnection) -> int:
    keys = (
        await db.execute(
            text(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
                "REFERENCED_COLUMN_NAME, REFERENCED_TABLE_SCHEMA "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL "
                "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION"
            )
        )
    ).all()
    database = (await db.execute(text("SELECT DATABASE()"))).scalar_one()
    grouped = defaultdict(list)
    for child, constraint, column, parent, referenced, schema in keys:
        if schema != database:
            raise BackupError(Reason.VALIDATION)
        grouped[(child, constraint, parent)].append((column, referenced))
    for (child, _, parent), columns in grouped.items():
        join = " AND ".join(
            f"c.{identifier(left)}=p.{identifier(right)}" for left, right in columns
        )
        present = " AND ".join(f"c.{identifier(left)} IS NOT NULL" for left, _ in columns)
        missing = f"p.{identifier(columns[0][1])} IS NULL"
        query = (
            f"SELECT 1 FROM {identifier(child)} c LEFT JOIN {identifier(parent)} p ON {join} "
            f"WHERE {present} AND {missing} LIMIT 1"
        )
        if (await db.execute(text(query))).first() is not None:
            raise BackupError(Reason.VALIDATION)
    return len(grouped)


async def verify_database(db: AsyncConnection, manifest: Manifest, deadline: float) -> dict:
    schema = await read_schema(db)
    if (schema.charset, schema.collation) != (manifest.source.charset, manifest.source.collation):
        raise BackupError(Reason.VALIDATION)
    enabled = (await db.execute(text("SELECT @@session.foreign_key_checks"))).scalar_one()
    if enabled != 1:
        raise BackupError(Reason.VALIDATION)
    expected = {table.name: table for table in manifest.source.tables}
    schema_hash = hashlib.sha256()
    counts = {}
    generated = 0
    for table in schema.tables:
        original = expected[table.name]
        if (table.columns, table.writable, table.primary_key, normalized_ddl(table.ddl)) != (
            original.columns,
            original.writable,
            original.primary_key,
            normalized_ddl(original.ddl),
        ):
            raise BackupError(Reason.VALIDATION)
        schema_hash.update(normalized_ddl(table.ddl).encode())
        generated += len(table.columns) - len(table.writable)
        digest, rows = hashlib.sha256(), 0
        async for record in row_records(db, table):
            if time.monotonic() >= deadline:
                raise BackupError(Reason.TIMEOUT)
            digest.update(record)
            rows += 1
        if (
            rows != manifest.files[table.name].rows
            or digest.hexdigest() != manifest.files[table.name].sha256
        ):
            raise BackupError(Reason.VALIDATION)
        counts[table.name] = rows
    return dict(
        table_counts=counts,
        schema_sha256=schema_hash.hexdigest(),
        foreign_keys_checked=await foreign_keys(db),
        generated_columns_checked=generated,
        utc_verified=True,
        schema_verified=True,
        row_hashes_verified=True,
    )
