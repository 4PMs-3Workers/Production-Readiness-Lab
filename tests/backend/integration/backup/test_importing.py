import time

import pytest
from sqlalchemy import text

from app.backup.backup import backup
from app.backup.config import database_name
from app.backup.importing import create_tables, insert_rows
from app.backup.target import empty_target

pytestmark = pytest.mark.mysql


async def test_schema_and_all_row_types_restore_without_seed_including_generated_guard(
    recovery_config, graph
):
    _, source_rows = graph
    manifest = await backup(recovery_config)
    async with empty_target(
        recovery_config, manifest, database_name(recovery_config.target_url)
    ) as db:
        try:
            await create_tables(db, manifest, 10)
            async with db.begin():
                counts = await insert_rows(
                    db,
                    manifest,
                    recovery_config.store / manifest.backup_id,
                    10,
                    time.monotonic() + 30,
                )
            assert all(count == manifest.files[name].rows for name, count in counts.items())
            row = (
                await db.execute(
                    text("SELECT id, starts_at, creation_request_hash FROM appointments")
                )
            ).one()
            assert row[0] == source_rows["appointments"].id.bytes
            assert row[2] == source_rows["appointments"].creation_request_hash
            assert row[1].microsecond == 123456
            assert (
                await db.execute(text("SELECT pending_guard FROM change_proposals"))
            ).scalar_one() == 1
            assert (
                await db.execute(text("SELECT metadata FROM audit_events"))
            ).scalar_one() == "{}"
            assert (await db.execute(text("SELECT @@session.time_zone"))).scalar_one() == "+00:00"
        finally:
            await db.rollback()
            await db.execute(text("SET SESSION FOREIGN_KEY_CHECKS=1"))
            await db.commit()
