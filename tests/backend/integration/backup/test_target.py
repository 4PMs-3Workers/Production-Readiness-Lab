from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.backup.backup import backup
from app.backup.config import BackupError, Reason, database_name
from app.backup.target import empty_target

pytestmark = pytest.mark.mysql


async def test_current_source_and_mismatched_targets_are_refused(recovery_config):
    manifest = await backup(recovery_config)
    for config, target in (
        (
            replace(recovery_config, target_url=recovery_config.database_url),
            manifest.source.database,
        ),
        (recovery_config, "other_target"),
    ):
        with pytest.raises(BackupError) as error:
            async with empty_target(config, manifest, target):
                pytest.fail("Wrong target admitted")
        assert error.value.reason == Reason.WRONG_TARGET


async def test_separately_named_empty_target_is_admitted_then_nonempty_refused(recovery_config):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    async with empty_target(recovery_config, manifest, target) as db:
        assert (await db.execute(text("SELECT DATABASE()"))).scalar_one() == target
    engine = create_async_engine(recovery_config.target_url)
    try:
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE keep_existing(id INT PRIMARY KEY) ENGINE=InnoDB"))
        with pytest.raises(BackupError) as error:
            async with empty_target(recovery_config, manifest, target):
                pytest.fail("Nonempty target admitted")
        assert error.value.reason == Reason.NONEMPTY
    finally:
        await engine.dispose()
