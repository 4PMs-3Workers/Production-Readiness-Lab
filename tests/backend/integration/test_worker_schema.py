import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.worker.resources import SchemaCompatibilityError, WorkerResources

pytestmark = pytest.mark.mysql
HEAD = "20261019_1200_proposal_replay"


async def test_schema_gate_only_reads_current_migrated_schema(mysql_engine, auth_settings):
    resources = WorkerResources(mysql_engine, async_sessionmaker(mysql_engine))
    settings = auth_settings.model_copy(update={"accepted_schema_revisions_raw": HEAD})
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(mysql_engine.sync_engine, "before_cursor_execute", record)
    try:
        await resources.check_compatibility(settings)
    finally:
        event.remove(mysql_engine.sync_engine, "before_cursor_execute", record)
    assert statements and all(statement.startswith("SELECT") for statement in statements)


@pytest.mark.parametrize("revision", [None, "a1b2c3d4e5f6", "unknown-release"])
async def test_schema_gate_rejects_missing_old_or_unknown_revision(
    mysql_engine, auth_settings, revision
):
    resources = WorkerResources(mysql_engine, async_sessionmaker(mysql_engine))
    async with mysql_engine.begin() as connection:
        await connection.execute(text("DELETE FROM alembic_version"))
        if revision:
            await connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
    settings = auth_settings.model_copy(update={"accepted_schema_revisions_raw": revision or HEAD})
    with pytest.raises(SchemaCompatibilityError):
        await resources.check_compatibility(settings)


async def test_schema_gate_requires_release_to_accept_the_worker_revision(
    mysql_engine, auth_settings
):
    with pytest.raises(SchemaCompatibilityError):
        await WorkerResources(mysql_engine, async_sessionmaker(mysql_engine)).check_compatibility(
            auth_settings
        )
