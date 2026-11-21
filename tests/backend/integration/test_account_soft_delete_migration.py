"""The release rollback may remove the soft-delete schema only when it holds no tombstones."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deploy import SOFT_DELETE_PREDECESSOR, SOFT_DELETE_SCHEMA, migrate
from app.models import User


@pytest.mark.mysql
async def test_guarded_schema_rollback_preserves_account_and_refuses_tombstones(
    mysql_engine, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", mysql_engine.url.render_as_string(hide_password=False))
    account_id = uuid4()
    async with AsyncSession(mysql_engine) as db, db.begin():
        db.add(
            User(
                id=account_id,
                email="rollback-account@example.invalid",
                name="Rollback fixture",
                role="STUDENT",
                is_active=True,
                password_hash="fixture-password-hash",
            )
        )
        await db.flush()
        # Use a single update to create a consistent deleted state.
        await db.execute(
            text(
                "UPDATE users SET deleted_at = UTC_TIMESTAMP(6), "
                "is_active_before_delete = 1, is_active = 0 WHERE id = :id"
            ),
            {"id": account_id.bytes},
        )

    with pytest.raises(RuntimeError, match="lifecycle state would be lost"):
        await migrate(rollback_soft_delete=True)

    async with mysql_engine.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            SOFT_DELETE_SCHEMA
        )
        account = (
            await connection.execute(
                text(
                    "SELECT deleted_at, is_active_before_delete, is_active "
                    "FROM users WHERE id = :id"
                ),
                {"id": account_id.bytes},
            )
        ).one()
        assert account.deleted_at is not None and account.is_active_before_delete
        assert not account.is_active
        # Restore the fixture account, keeping the original user row in place.
        await connection.execute(
            text(
                "UPDATE users SET deleted_at = NULL, is_active_before_delete = NULL, "
                "is_active = 1 WHERE id = :id"
            ),
            {"id": account_id.bytes},
        )

    await migrate(rollback_soft_delete=True)
    async with mysql_engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            SOFT_DELETE_PREDECESSOR
        )
        assert (
            await connection.scalar(
                text("SELECT email FROM users WHERE id = :id"), {"id": account_id.bytes}
            )
            == "rollback-account@example.invalid"
        )

    await migrate()
    async with AsyncSession(mysql_engine) as db:
        account = await db.get(User, account_id)
        assert account is not None and account.is_active and account.deleted_at is None
