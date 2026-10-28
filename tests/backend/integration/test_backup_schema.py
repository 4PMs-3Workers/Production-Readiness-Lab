from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.backup.config import BackupConfig, BackupError, Reason
from app.backup.schema import migration_contract, open_source
from app.core.transactions import mysql_error_code

pytestmark = pytest.mark.mysql


def config_for(engine, tmp_path):
    return BackupConfig(engine.url.render_as_string(hide_password=False), tmp_path)


async def test_backup_preflight_uses_current_head_and_does_not_write_source(mysql_engine, tmp_path):
    async with open_source(config_for(mysql_engine, tmp_path)) as (db, schema):
        assert (schema.revision, schema.migration_sha256) == migration_contract()
        assert schema.server_version.startswith("8.4.")
        assert any(table.name == "change_proposals" for table in schema.tables)
        assert (await db.execute(text("SELECT @@session.time_zone"))).scalar_one() == "+00:00"


async def test_backup_refuses_unknown_revision_and_partial_column_ddl(mysql_engine, tmp_path):
    config = config_for(mysql_engine, tmp_path)
    async with mysql_engine.begin() as db:
        await db.execute(text("ALTER TABLE users ADD COLUMN unreviewed INT NULL"))
    with pytest.raises(BackupError) as error:
        async with open_source(config):
            pytest.fail("Partial/unreviewed schema accepted")
    assert error.value.reason == Reason.UNSUPPORTED
    async with mysql_engine.begin() as db:
        await db.execute(text("UPDATE alembic_version SET version_num='unknown'"))
    with pytest.raises(BackupError) as error:
        async with open_source(replace(config, query_seconds=5)):
            pytest.fail("Unknown revision accepted")
    assert error.value.reason == Reason.INCOMPATIBLE


async def test_native_backup_lock_blocks_ddl_while_ordinary_writes_continue(mysql_engine, tmp_path):
    async with open_source(config_for(mysql_engine, tmp_path)):
        async with mysql_engine.connect() as writer:
            await writer.execute(text("UPDATE users SET name=name"))
            await writer.commit()
            await writer.execute(text("SET SESSION lock_wait_timeout=1"))
            with pytest.raises(OperationalError) as error:
                await writer.execute(text("ALTER TABLE users ADD COLUMN blocked_ddl INT"))
            assert mysql_error_code(error.value) == 1205
