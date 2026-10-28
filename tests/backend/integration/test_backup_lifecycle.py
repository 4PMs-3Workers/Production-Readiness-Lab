import asyncio

import pytest
from sqlalchemy import text

from app.core.schema_lifecycle import (
    SchemaLifecycleBusy,
    lock_name,
    migration_connection,
    schema_lifecycle,
)

pytestmark = pytest.mark.mysql


async def test_schema_lifecycle_excludes_second_owner_but_not_ordinary_reads(mysql_engine):
    async with mysql_engine.connect() as first, mysql_engine.connect() as second:
        async with schema_lifecycle(first):
            assert (await second.execute(text("SELECT 1"))).scalar_one() == 1
            await second.rollback()
            async with asyncio.timeout(2):
                with pytest.raises(SchemaLifecycleBusy):
                    async with schema_lifecycle(second, timeout=0):
                        pytest.fail("Overlapping lifecycle owner admitted")
        await second.rollback()
        async with schema_lifecycle(second, timeout=0):
            assert (await second.execute(text("SELECT 1"))).scalar_one() == 1


async def test_migration_connection_commits_before_releasing_guard(mysql_engine):
    async with migration_connection(mysql_engine) as connection:
        name = (await connection.execute(text("SELECT DATABASE()"))).scalar_one()
        assert (
            await connection.execute(text("SELECT IS_USED_LOCK(:name)"), {"name": lock_name(name)})
        ).scalar_one() is not None
    async with mysql_engine.connect() as observer:
        assert (
            await observer.execute(text("SELECT IS_USED_LOCK(:name)"), {"name": lock_name(name)})
        ).scalar_one() is None
