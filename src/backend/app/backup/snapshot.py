"""One nonlocking InnoDB read view supplies every table and verification count."""

import asyncio
import hashlib
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.backup.codec import encode_row
from app.backup.config import BackupError, Reason, identifier
from app.backup.schema import SourceSchema, TableSchema
from app.backup.storage import capacity


def utc(value: datetime) -> str:
    instant = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


@asynccontextmanager
async def consistent_snapshot(db: AsyncConnection) -> AsyncIterator[dict[str, str]]:
    await db.rollback()
    await db.execution_options(isolation_level="REPEATABLE READ")
    before = (await db.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()
    await db.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
    try:
        after = (await db.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()
        yield {"established_before": utc(before), "established_after": utc(after)}
    finally:
        await db.rollback()


async def row_records(
    db: AsyncConnection, table: TableSchema, query_seconds: int = 30
) -> AsyncGenerator[bytes, None]:
    columns = ",".join(identifier(column) for column in table.columns)
    keys = ",".join(identifier(column) for column in table.primary_key)
    sql = f"SELECT {columns} FROM {identifier(table.name)} ORDER BY {keys}"
    async with asyncio.timeout(query_seconds):
        async with db.stream(
            text(sql), execution_options={"yield_per": 1, "max_row_buffer": 1}
        ) as rows:
            async for row in rows:
                yield encode_row(row)


async def dump_tables(
    db: AsyncConnection,
    schema: SourceSchema,
    directory: Path,
    max_bytes: int,
    min_free_bytes: int = 0,
    query_seconds: int = 30,
) -> dict[str, dict]:
    result, total = {}, 0
    for table in schema.tables:
        path = directory / f"{table.name}.jsonl"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest, count, size = hashlib.sha256(), 0, 0
        with os.fdopen(descriptor, "wb") as output:
            async with aclosing(row_records(db, table, query_seconds)) as records:
                async for record in records:
                    total += len(record)
                    if total > max_bytes:
                        raise BackupError(Reason.STORAGE)
                    capacity(directory, len(record), min_free_bytes)
                    output.write(record)
                    digest.update(record)
                    size += len(record)
                    count += 1
            output.flush()
            os.fsync(output.fileno())
        result[table.name] = {"rows": count, "bytes": size, "sha256": digest.hexdigest()}
    return result
