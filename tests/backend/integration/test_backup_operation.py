from dataclasses import replace

import pytest
from sqlalchemy import text
from tests.backend.integration.test_backup_schema import config_for

from app.backup.backup import backup
from app.backup.config import BackupError, Reason
from app.backup.storage import load_artifact

pytestmark = pytest.mark.mysql


async def test_online_backup_publishes_verified_current_schema_and_snapshot_counts(
    mysql_engine, tmp_path
):
    config = config_for(mysql_engine, tmp_path)
    result = await backup(config)
    restored = load_artifact(config, result.backup_id)
    assert restored == result and result.files["alembic_version"].rows == 1
    assert result.source.database == mysql_engine.url.database
    assert not list(tmp_path.glob(".partial-*"))
    async with mysql_engine.connect() as observer:
        assert (
            await observer.execute(text("SELECT @@transaction_isolation"))
        ).scalar_one() == "READ-COMMITTED"


async def test_capacity_failure_publishes_nothing_and_releases_source_locks(mysql_engine, tmp_path):
    config = replace(config_for(mysql_engine, tmp_path), max_bytes=1024)
    with pytest.raises(BackupError) as error:
        await backup(config)
    assert error.value.reason == Reason.STORAGE
    assert list(tmp_path.iterdir()) == []
    assert (await backup(replace(config, max_bytes=1000000))).state == "complete"
