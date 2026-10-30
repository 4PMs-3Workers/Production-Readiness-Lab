from uuid import uuid4

import pytest
from sqlalchemy import text
from tests.backend.integration.test_backup_schema import config_for

from app.backup.codec import decode_row
from app.backup.schema import open_source
from app.backup.snapshot import consistent_snapshot, dump_tables
from app.models import Purpose, User

pytestmark = pytest.mark.mysql


async def test_snapshot_data_and_counts_ignore_later_committed_writes(mysql_engine, tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(mysql_engine) as session:
        session.add_all(
            [
                User(
                    id=uuid4(),
                    email="fictional@backup.invalid",
                    name="before",
                    role="STUDENT",
                    password_hash="not-used",
                ),
                Purpose(id=uuid4(), code="BACKUP", name="before", duration_minutes=30),
            ]
        )
        await session.commit()
    async with open_source(config_for(mysql_engine, tmp_path)) as (reader, schema):
        async with consistent_snapshot(reader) as point:
            # These ordinary writes commit while the backup snapshot is already held.
            async with mysql_engine.begin() as writer:
                await writer.execute(text("UPDATE users SET name='after'"))
                await writer.execute(text("UPDATE purposes SET name='after'"))
            records = await dump_tables(reader, schema, tmp_path, 1000000)
            assert point["established_before"] <= point["established_after"]
    for table in (item for item in schema.tables if item.name in {"users", "purposes"}):
        rows = [
            decode_row(line, len(table.columns))
            for line in (tmp_path / f"{table.name}.jsonl").read_bytes().splitlines(keepends=True)
        ]
        assert len(rows) == records[table.name]["rows"] == 1
        assert rows[0][table.columns.index("name")] == "before"
    async with mysql_engine.connect() as observer:
        assert (await observer.execute(text("SELECT name FROM users"))).scalar_one() == "after"
