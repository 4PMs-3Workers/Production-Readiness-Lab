from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.backend.integration.backup.grant_helpers import (
    assert_literal_grant,
    scoped_account,
)

from app.backup.backup import backup
from app.backup.config import BackupError, Reason, database_name
from app.backup.schema import open_source, require_metadata_visibility
from app.models import Base

pytestmark = pytest.mark.mysql


async def test_escaped_underscore_grants_allow_real_source_snapshot(
    mysql_engine, recovery_config, graph
):
    database = database_name(recovery_config.database_url)
    sibling = database.replace("_", "X", 1)
    # This sibling would be accessible through the unescaped wildcard workaround.
    async with mysql_engine.begin() as db:
        await db.execute(text(f"CREATE DATABASE `{sibling}`"))
        await db.execute(text(f"CREATE TABLE `{sibling}`.users (id INT PRIMARY KEY)"))
        original = (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one()
    try:
        async with scoped_account(mysql_engine, database) as account:
            await assert_literal_grant(account, database)
            async with account.connect() as db:
                assert (
                    await db.execute(text("SELECT COUNT(*) FROM users"))
                ).scalar_one() == original
                with pytest.raises(DBAPIError):
                    await db.execute(text(f"SELECT COUNT(*) FROM `{sibling}`.users"))
            config = replace(
                recovery_config, database_url=account.url.render_as_string(hide_password=False)
            )
            async with open_source(config) as (_, schema):
                assert {table.name for table in schema.tables} == set(Base.metadata.tables) | {
                    "alembic_version"
                }
            manifest = await backup(config)
            assert manifest.files["users"].rows == original == 2
            assert manifest.files["appointments"].rows == 1
            assert manifest.files["change_proposals"].rows == 1
            assert manifest.files["audit_events"].rows == 1
            assert manifest.files["notifications"].rows == 1
        async with mysql_engine.connect() as db:
            assert (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one() == original
    finally:
        async with mysql_engine.begin() as db:
            await db.execute(text(f"DROP DATABASE `{sibling}`"))


@pytest.mark.parametrize("rights", ["SELECT", "SELECT, TRIGGER", "SELECT, EVENT"])
async def test_escaped_source_grants_still_require_all_metadata_privileges(
    mysql_engine, recovery_config, rights
):
    database = database_name(recovery_config.database_url)
    async with scoped_account(mysql_engine, database, rights) as account:
        await assert_literal_grant(account, database)
        config = replace(
            recovery_config, database_url=account.url.render_as_string(hide_password=False)
        )
        with pytest.raises(BackupError) as error:
            await backup(config)
        assert error.value.reason == Reason.UNSUPPORTED
        assert not list(config.store.glob("prl-backup-*"))
        assert not list(config.store.glob(".partial-*"))


async def test_literal_source_grant_takes_precedence_over_broader_pattern(
    mysql_engine, recovery_config
):
    database = database_name(recovery_config.database_url)
    async with scoped_account(
        mysql_engine,
        database,
        "SELECT",
        extra_scopes=((f"`{database}`.*", "TRIGGER, EVENT"),),
    ) as account:
        async with account.connect() as db:
            assert (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one() == 0
            with pytest.raises(BackupError) as error:
                await require_metadata_visibility(db)
            assert error.value.reason == Reason.UNSUPPORTED


async def test_escaped_full_source_grants_do_not_hide_unsupported_objects(
    mysql_engine, recovery_config
):
    database = database_name(recovery_config.database_url)
    async with scoped_account(mysql_engine, database) as account:
        async with account.connect() as db:
            await require_metadata_visibility(db)
        async with mysql_engine.begin() as db:
            await db.execute(
                text(
                    "CREATE TRIGGER grant_probe_trigger BEFORE UPDATE ON users "
                    "FOR EACH ROW SET NEW.name=NEW.name"
                )
            )
        config = replace(
            recovery_config, database_url=account.url.render_as_string(hide_password=False)
        )
        with pytest.raises(BackupError) as error:
            async with open_source(config):
                pytest.fail("An unsupported trigger was accepted")
        assert error.value.reason == Reason.UNSUPPORTED
