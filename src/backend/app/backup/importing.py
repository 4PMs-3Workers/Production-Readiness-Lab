"""Restore reviewed DDL and bound row parameters, never run seed or downgrade."""

import asyncio
import hashlib
import os
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.backup.codec import MAX_RECORD_BYTES, decode_row
from app.backup.config import BackupError, Reason, identifier
from app.backup.manifest import Manifest


async def create_tables(db: AsyncConnection, manifest: Manifest, query_seconds: int) -> None:
    await db.execute(text("SET SESSION FOREIGN_KEY_CHECKS=0"))
    for table in manifest.source.tables:
        async with asyncio.timeout(query_seconds):
            # Validated single CREATE TABLE, including exact index/constraint and
            # generated-column definitions. Never interpolate row values into SQL.
            await db.exec_driver_sql(table.ddl)
    await db.commit()


async def insert_rows(
    db: AsyncConnection, manifest: Manifest, directory: Path, query_seconds: int, deadline: float
) -> dict[str, int]:
    if not db.in_transaction():
        raise RuntimeError("Restore rows require a caller-owned transaction")
    counts = {}
    for table in manifest.source.tables:
        positions = [table.columns.index(column) for column in table.writable]
        columns = ",".join(identifier(column) for column in table.writable)
        parameters = ",".join(f":v{index}" for index in range(len(positions)))
        statement = text(f"INSERT INTO {identifier(table.name)} ({columns}) VALUES ({parameters})")
        descriptor = os.open(
            directory / f"{table.name}.jsonl", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        digest, count = hashlib.sha256(), 0
        with os.fdopen(descriptor, "rb") as source:
            while record := source.readline(MAX_RECORD_BYTES + 1):
                if time.monotonic() >= deadline:
                    raise BackupError(Reason.TIMEOUT)
                row = decode_row(record, len(table.columns))
                values = {f"v{index}": row[position] for index, position in enumerate(positions)}
                async with asyncio.timeout(query_seconds):
                    await db.execute(statement, values)
                digest.update(record)
                count += 1
        if (
            count != manifest.files[table.name].rows
            or digest.hexdigest() != manifest.files[table.name].sha256
        ):
            raise BackupError(Reason.INTEGRITY)
        counts[table.name] = count
    return counts
