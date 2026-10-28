import asyncio
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.backup.backup import backup
from app.backup.config import BackupError, Reason, database_name
from app.backup.restore import restore
from app.core.database import create_database_engine
from app.core.transactions import mysql_error_code

pytestmark = pytest.mark.mysql


async def target_table_count(config):
    engine = create_database_engine(config.target_url)
    try:
        async with engine.connect() as db:
            return (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA=DATABASE()"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "fault", ["truncate", "missing-manifest", "missing-marker", "schema", "release"]
)
async def test_artifact_preflight_failure_never_writes_target_or_source(
    recovery_config, graph, fault
):
    manifest = await backup(recovery_config)
    directory = recovery_config.store / manifest.backup_id
    config = recovery_config
    if fault == "truncate":
        (directory / "users.jsonl").write_bytes(b"[")
    elif fault == "missing-manifest":
        (directory / "manifest.json").unlink()
    elif fault == "missing-marker":
        (directory / "COMPLETE").unlink()
    elif fault == "release":
        config = replace(config, source_sha="f" * 40)
    else:
        value = json.loads((directory / "manifest.json").read_bytes())
        value["source"]["revision"] = "unknown"
        raw = json.dumps(value).encode()
        (directory / "manifest.json").write_bytes(raw)
        (directory / "COMPLETE").write_bytes(hashlib.sha256(raw).hexdigest().encode())
    with pytest.raises(BackupError):
        await restore(config, manifest.backup_id, database_name(config.target_url))
    assert await target_table_count(config) == 0
    session, _ = graph
    assert (await session.execute(text("SELECT COUNT(*) FROM appointments"))).scalar_one() == 1
    assert (
        await session.execute(text("SELECT status FROM change_proposals"))
    ).scalar_one() == "PENDING"


async def test_precommit_restore_failure_rolls_back_data_and_quarantines_target(
    recovery_config, graph, monkeypatch
):
    from app.backup import restore as module

    manifest = await backup(recovery_config)

    async def failed_import(db, *args):
        await db.execute(text("INSERT INTO alembic_version(version_num) VALUES ('partial')"))
        raise OperationalError("private SQL", {}, Exception(2003, "private credentials"))

    monkeypatch.setattr(module, "insert_rows", failed_import)
    target = database_name(recovery_config.target_url)
    with pytest.raises(BackupError) as error:
        await restore(recovery_config, manifest.backup_id, target)
    assert error.value.reason == Reason.DATABASE
    assert await target_table_count(recovery_config) == len(manifest.files)
    engine = create_database_engine(recovery_config.target_url)
    try:
        async with engine.connect() as db:
            assert (
                await db.execute(text("SELECT COUNT(*) FROM alembic_version"))
            ).scalar_one() == 0
    finally:
        await engine.dispose()
    with pytest.raises(BackupError) as error:
        await restore(recovery_config, manifest.backup_id, target)
    assert error.value.reason == Reason.NONEMPTY


async def test_restore_timeout_is_bounded_and_does_not_report_success(recovery_config, monkeypatch):
    from app.backup import restore as module

    manifest = await backup(recovery_config)

    async def blocked(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "insert_rows", blocked)
    async with asyncio.timeout(5):
        with pytest.raises(BackupError) as error:
            await restore(
                replace(recovery_config, max_seconds=1),
                manifest.backup_id,
                database_name(recovery_config.target_url),
            )
    assert error.value.reason == Reason.TIMEOUT


@pytest.mark.parametrize("constraint", ["CHECK", "FK", "UNIQUE"])
async def test_recovered_constraints_are_enforced_by_mysql(recovery_config, graph, constraint):
    manifest = await backup(recovery_config)
    await restore(recovery_config, manifest.backup_id, database_name(recovery_config.target_url))
    engine = create_database_engine(recovery_config.target_url)
    try:
        with pytest.raises((IntegrityError, OperationalError)) as error:
            async with engine.begin() as db:
                if constraint == "CHECK":
                    await db.execute(text("UPDATE appointments SET duration_minutes=0"))
                elif constraint == "FK":
                    await db.execute(
                        text("UPDATE appointments SET student_id=:id"), {"id": uuid4().bytes}
                    )
                else:
                    await db.execute(text("UPDATE users SET email='duplicate@backup.invalid'"))
        assert (
            mysql_error_code(error.value) == {"CHECK": 3819, "FK": 1452, "UNIQUE": 1062}[constraint]
        )
    finally:
        await engine.dispose()
