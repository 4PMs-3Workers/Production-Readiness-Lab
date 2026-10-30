import time
from dataclasses import replace

import pytest
from sqlalchemy import text
from tests.backend.integration.backup.grant_helpers import (
    assert_literal_grant,
    literal_scope,
    scoped_account,
)

from app.backup.backup import backup
from app.backup.config import BackupError, Reason, database_name
from app.backup.restore import restore, verify_restored
from app.backup.snapshot import consistent_snapshot
from app.backup.target import empty_target
from app.backup.verification import verify_database

pytestmark = pytest.mark.mysql
RESTORE_RIGHTS = "SELECT, INSERT, CREATE, INDEX, REFERENCES, TRIGGER, EVENT"


async def table_count(administrator, database):
    async with administrator.connect() as db:
        return (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.TABLES " "WHERE TABLE_SCHEMA=:database"
                ),
                {"database": database},
            )
        ).scalar_one()


async def test_escaped_target_grants_allow_preflight_restore_and_read_only_verification(
    mysql_engine, recovery_config, graph
):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    async with scoped_account(
        mysql_engine, target, RESTORE_RIGHTS, global_privileges="SHOW_ROUTINE"
    ) as account:
        await assert_literal_grant(account, target)
        config = replace(
            recovery_config, target_url=account.url.render_as_string(hide_password=False)
        )
        async with empty_target(config, manifest, target) as db:
            assert (await db.execute(text("SELECT DATABASE()"))).scalar_one() == target
        result = await restore(config, manifest.backup_id, target)
        read_only = await verify_restored(config, manifest.backup_id, target)
        assert result["row_hashes_verified"] and read_only["row_hashes_verified"]
        assert (
            result["table_counts"]
            == {name: entry.rows for name, entry in manifest.files.items()}
            == read_only["table_counts"]
        )
        assert result["foreign_keys_checked"] > 0 and result["generated_columns_checked"] == 1
        assert result["utc_verified"] and result["schema_verified"]
    # Every original row and definition must still match the source artifact.
    async with mysql_engine.connect() as db:
        async with consistent_snapshot(db):
            source = await verify_database(db, manifest, time.monotonic() + 30)
    assert source["row_hashes_verified"] and source["table_counts"] == result["table_counts"]


async def test_escaped_target_grants_still_refuse_nonempty_database(mysql_engine, recovery_config):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    async with mysql_engine.begin() as db:
        await db.execute(text(f"CREATE TABLE `{target}`.keep_existing (id INT PRIMARY KEY)"))
        await db.execute(text(f"INSERT INTO `{target}`.keep_existing VALUES (17)"))
    async with scoped_account(
        mysql_engine, target, RESTORE_RIGHTS, global_privileges="SHOW_ROUTINE"
    ) as account:
        await assert_literal_grant(account, target)
        config = replace(
            recovery_config, target_url=account.url.render_as_string(hide_password=False)
        )
        with pytest.raises(BackupError) as error:
            await restore(config, manifest.backup_id, target)
        assert error.value.reason == Reason.NONEMPTY
        async with account.connect() as db:
            assert (await db.execute(text("SELECT id FROM keep_existing"))).scalar_one() == 17
    assert await table_count(mysql_engine, target) == 1


@pytest.mark.parametrize(
    "rights", ["SELECT, CREATE", "SELECT, CREATE, TRIGGER", "SELECT, CREATE, EVENT"]
)
async def test_escaped_target_grants_without_metadata_are_refused_before_ddl(
    mysql_engine, recovery_config, rights
):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    async with scoped_account(
        mysql_engine, target, rights, global_privileges="SHOW_ROUTINE"
    ) as account:
        await assert_literal_grant(account, target)
        config = replace(
            recovery_config, target_url=account.url.render_as_string(hide_password=False)
        )
        with pytest.raises(BackupError) as error:
            await restore(config, manifest.backup_id, target)
        assert error.value.reason == Reason.UNSUPPORTED
    assert await table_count(mysql_engine, target) == 0


async def test_metadata_granted_on_other_schema_does_not_authorize_target_writes(
    mysql_engine, recovery_config
):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    source = database_name(recovery_config.database_url)
    async with scoped_account(
        mysql_engine,
        target,
        "SELECT, CREATE",
        extra_scopes=((literal_scope(source), "TRIGGER, EVENT"),),
        global_privileges="SHOW_ROUTINE",
    ) as account:
        await assert_literal_grant(account, target)
        config = replace(
            recovery_config, target_url=account.url.render_as_string(hide_password=False)
        )
        with pytest.raises(BackupError) as error:
            await restore(config, manifest.backup_id, target)
        assert error.value.reason == Reason.UNSUPPORTED
    assert await table_count(mysql_engine, target) == 0
