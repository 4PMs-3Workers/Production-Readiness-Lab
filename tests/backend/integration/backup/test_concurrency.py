import asyncio
import importlib
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.backup.backup import backup
from app.backup.config import BackupError, Reason
from app.backup.restore import restore

pytestmark = pytest.mark.mysql


async def names(engine):
    async with engine.connect() as db:
        users = (await db.execute(text("SELECT name FROM users ORDER BY id"))).scalars().all()
        purpose = (await db.execute(text("SELECT name FROM purposes"))).scalar_one()
        return list(users), purpose


async def test_complete_restore_remains_consistent_during_continuous_transactions(
    mysql_engine, recovery_config, graph, monkeypatch
):
    module = importlib.import_module("app.backup.backup")
    original_dump = module.dump_tables
    entered, progressed, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = 0

    async def writer():
        nonlocal writes
        await entered.wait()
        while not stop.is_set():
            async with mysql_engine.begin() as db:
                name = f"concurrent-{writes}"
                await db.execute(text("UPDATE users SET name=:name"), {"name": name})
                await db.execute(text("UPDATE purposes SET name=:name"), {"name": name})
            writes += 1
            if writes >= 3:
                progressed.set()
            await asyncio.sleep(0.001)

    async def dump(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(progressed.wait(), 10)
        return await original_dump(*args, **kwargs)

    # The starting state also represents one committed cross-table transaction.
    async with mysql_engine.begin() as db:
        await db.execute(text("UPDATE users SET name='initial'"))
        await db.execute(text("UPDATE purposes SET name='initial'"))
    monkeypatch.setattr(module, "dump_tables", dump)
    task = asyncio.create_task(writer())
    try:
        artifact = await asyncio.wait_for(backup(recovery_config), 30)
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)
    assert writes >= 3
    source_after = await names(mysql_engine)
    target = create_async_engine(recovery_config.target_url)
    try:
        database = target.url.database
        assert database is not None
        await restore(recovery_config, artifact.backup_id, database)
        recovered_users, recovered_purpose = await names(target)
        assert set(recovered_users) == {recovered_purpose}
        assert recovered_purpose == "initial"
        assert await names(mysql_engine) == source_after
    finally:
        await target.dispose()


async def test_missing_native_backup_privilege_fails_without_publishing(
    mysql_engine, recovery_config
):
    user = "backup_" + uuid4().hex[:20]
    async with mysql_engine.begin() as db:
        await db.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY 'fictional-test-password'"))
        await db.execute(text(f"GRANT SELECT ON `{mysql_engine.url.database}`.* TO '{user}'@'%'"))
    config = replace(
        recovery_config,
        database_url=mysql_engine.url.set(
            username=user, password="fictional-test-password"
        ).render_as_string(hide_password=False),
    )
    try:
        with pytest.raises(BackupError):
            await backup(config)
        assert not list(config.store.glob("prl-backup-*"))
        assert not list(config.store.glob(".partial-*"))
    finally:
        async with mysql_engine.begin() as db:
            await db.execute(text(f"DROP USER '{user}'@'%'"))


async def test_backup_cannot_mistake_filtered_metadata_for_absent_triggers(
    mysql_engine, recovery_config
):
    user = "backup_" + uuid4().hex[:20]
    database = mysql_engine.url.database
    async with mysql_engine.begin() as db:
        await db.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY 'fictional-test-password'"))
        await db.execute(text(f"GRANT SELECT ON `{database}`.* TO '{user}'@'%'"))
        await db.execute(text(f"GRANT BACKUP_ADMIN ON *.* TO '{user}'@'%'"))
    config = replace(
        recovery_config,
        database_url=mysql_engine.url.set(
            username=user, password="fictional-test-password"
        ).render_as_string(hide_password=False),
    )
    try:
        with pytest.raises(BackupError) as error:
            await backup(config)
        assert error.value.reason == Reason.UNSUPPORTED
        async with mysql_engine.begin() as db:
            await db.execute(text(f"GRANT TRIGGER,EVENT ON `{database}`.* TO '{user}'@'%'"))
            await db.execute(text(f"GRANT SHOW_ROUTINE ON *.* TO '{user}'@'%'"))
        manifest = await backup(config)
        async with mysql_engine.begin() as db:
            await db.execute(
                text(
                    "CREATE TRIGGER unsupported_trigger BEFORE UPDATE ON users "
                    "FOR EACH ROW SET NEW.name=NEW.name"
                )
            )
        with pytest.raises(BackupError) as error:
            await backup(config)
        assert error.value.reason == Reason.UNSUPPORTED
        assert [path.name for path in config.store.glob("prl-backup-*")] == [manifest.backup_id]
    finally:
        async with mysql_engine.begin() as db:
            await db.execute(text("DROP TRIGGER IF EXISTS unsupported_trigger"))
            await db.execute(text(f"DROP USER '{user}'@'%'"))
