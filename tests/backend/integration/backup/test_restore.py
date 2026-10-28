import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.backup.backup import backup
from app.backup.config import BackupError, Reason, database_name
from app.backup.restore import restore
from app.backup.verification import verify_database

pytestmark = pytest.mark.mysql


async def test_verified_restore_checks_all_rows_constraints_generated_columns_and_utc(
    recovery_config, graph, mysql_engine
):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    report = await restore(recovery_config, manifest.backup_id, target)
    assert report["table_counts"] == {name: file.rows for name, file in manifest.files.items()}
    assert report["foreign_keys_checked"] > 0 and report["generated_columns_checked"] == 1
    assert report["schema_verified"] and report["utc_verified"] and report["row_hashes_verified"]
    async with mysql_engine.connect() as source:
        assert (await source.execute(text("SELECT COUNT(*) FROM appointments"))).scalar_one() == 1
    with pytest.raises(BackupError) as error:
        await restore(recovery_config, manifest.backup_id, target)
    assert error.value.reason == Reason.NONEMPTY


async def test_changes_to_recovered_rows_are_detected_against_snapshot_not_live_source(
    recovery_config, graph
):
    manifest = await backup(recovery_config)
    target = database_name(recovery_config.target_url)
    await restore(recovery_config, manifest.backup_id, target)
    engine = create_async_engine(recovery_config.target_url)
    try:
        async with engine.begin() as db:
            await db.execute(text("UPDATE users SET name='changed-after-recovery'"))
        from app.core.database import create_database_engine

        checked = create_database_engine(recovery_config.target_url)
        try:
            async with checked.connect() as db:
                with pytest.raises(BackupError) as error:
                    await verify_database(db, manifest, time.monotonic() + 10)
                assert error.value.reason == Reason.VALIDATION
        finally:
            await checked.dispose()
    finally:
        await engine.dispose()
